from __future__ import annotations

import argparse
import json

from .atlas import build_atlas
from .catalog import backfill_official_details, import_official_records
from .db import connect, dump_json, initialize, paper_payload
from .enrich import enrich_arxiv, enrich_crossref_ieee, enrich_exa_public, enrich_openalex, harvest_arxiv_bulk
from .official import extract_official_paper_details, extract_official_records, fetch_official_html, fetch_official_schedule_data
from .server import serve


def main() -> None:
    parser = argparse.ArgumentParser(description="IROS 2026 paper catalog")
    parser.add_argument("--db", default="data/iros.sqlite", help="SQLite database path")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("atlas-build", help="Rebuild normalized Atlas keywords, entities, and scores")
    commands.add_parser("sync")
    commands.add_parser("sync-official-details", help="Backfill first-party IROS abstracts and keywords")
    enrich = commands.add_parser("enrich")
    enrich.add_argument("--limit", type=int)
    enrich.add_argument("--delay", type=float, default=0.8, help="Seconds between public-index requests (default: 0.8)")
    crossref = commands.add_parser("enrich-crossref")
    crossref.add_argument("--limit", type=int)
    crossref.add_argument("--delay", type=float, default=0.5, help="Seconds between Crossref metadata requests (default: 0.5)")
    arxiv = commands.add_parser("enrich-arxiv")
    arxiv.add_argument("--limit", type=int)
    arxiv.add_argument("--delay", type=float, default=3.0, help="Seconds between arXiv title-search requests (default: 3.0)")
    arxiv.add_argument("--retry-no-match", action="store_true", help="Run the fuzzy fallback on prior no-result records")
    bulk = commands.add_parser("harvest-arxiv")
    bulk.add_argument("--delay", type=float, default=3.0, help="Seconds between bulk arXiv category requests (default: 3.0)")
    bulk.add_argument("--max-results", type=int, default=2000, help="Maximum results per category request (default: 2000)")
    bulk.add_argument("--start", type=int, help="Use a specific page offset, then persist the following offset")
    bulk.add_argument("--categories", help="Comma-separated arXiv categories for a disjoint shard")
    query = commands.add_parser("query")
    query.add_argument("--q")
    query.add_argument("--topic")
    query.add_argument("--has-pdf", action="store_true")
    query.add_argument("--limit", type=int, default=25)
    public_queue = commands.add_parser("unresolved-public-pages")
    public_queue.add_argument("--limit", type=int, default=25)
    public_attempt = commands.add_parser("record-public-search")
    public_attempt.add_argument("paper_number")
    public_attempt.add_argument("--detail", required=True, help="Short evidence from the failed public-page search")
    exa = commands.add_parser("enrich-exa-public")
    exa.add_argument("--limit", type=int, default=100, help="Unresolved records to search (default: 100; maximum: 500)")
    exa.add_argument("--delay", type=float, default=0.2, help="Seconds between serial Exa requests (default: 0.2; capped at five requests/second)")
    exa.add_argument("--abstract-only", action="store_true", help="Backfill abstracts for already linked public pages without changing their URLs")
    exa.add_argument("--after-paper-number", type=int, help="Only search paper numbers after this value")
    exa.add_argument("--only-untried", action="store_true", help="Skip locally stored retries and search only papers with no prior public-page attempt")
    exa.add_argument("--until-exhausted", action="store_true", help="Repeat serial batches until the selected untried queue is empty")
    exa.add_argument("--author-relaxed-retry", action="store_true", help="Recovery pass: require first author and relaxed title-term overlap for first-attempt retries")
    server = commands.add_parser("serve")
    server.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    initialize(args.db)
    if args.command == "init":
        print(f"initialized {args.db}")
    elif args.command == "atlas-build":
        print(dump_json(build_atlas(args.db)))
    elif args.command == "sync":
        records = extract_official_records(fetch_official_html())
        print(dump_json({"imported": import_official_records(args.db, records), "source": "official_iros_index"}))
    elif args.command == "sync-official-details":
        details = extract_official_paper_details(fetch_official_schedule_data())
        print(dump_json({**backfill_official_details(args.db, details), "source": "official_iros_schedule"}))
    elif args.command == "enrich":
        print(dump_json(enrich_openalex(args.db, args.limit, args.delay)))
    elif args.command == "enrich-crossref":
        print(dump_json(enrich_crossref_ieee(args.db, args.limit, args.delay)))
    elif args.command == "enrich-arxiv":
        print(dump_json(enrich_arxiv(args.db, args.limit, args.delay, args.retry_no_match)))
    elif args.command == "harvest-arxiv":
        categories = tuple(item.strip() for item in args.categories.split(",") if item.strip()) if args.categories else None
        print(dump_json(harvest_arxiv_bulk(args.db, args.delay, args.max_results, args.start, categories)))
    elif args.command == "query":
        with connect(args.db) as connection:
            conditions, values = [], []
            if args.q:
                conditions.append("p.paper_number IN (SELECT paper_number FROM papers_fts WHERE papers_fts MATCH ?)")
                values.append(args.q)
            if args.topic:
                conditions.append("EXISTS (SELECT 1 FROM paper_topics pt WHERE pt.paper_number=p.paper_number AND pt.topic=?)")
                values.append(args.topic)
            if args.has_pdf:
                conditions.append("p.pdf_url IS NOT NULL")
            sql = "SELECT p.* FROM papers p" + (" WHERE " + " AND ".join(conditions) if conditions else "") + " ORDER BY p.paper_number LIMIT ?"
            rows = connection.execute(sql, [*values, min(args.limit, 200)]).fetchall()
            print(dump_json([paper_payload(connection, row) for row in rows]))
    elif args.command == "unresolved-public-pages":
        with connect(args.db) as connection:
            rows = connection.execute(
                "SELECT * FROM papers WHERE public_page_url IS NULL AND public_link_attempts <= 2 "
                "ORDER BY CAST(paper_number AS INTEGER) LIMIT ?",
                (min(args.limit, 200),),
            ).fetchall()
            print(dump_json([paper_payload(connection, row) for row in rows]))
    elif args.command == "record-public-search":
        with connect(args.db) as connection:
            paper = connection.execute(
                "SELECT paper_number, title, public_page_url, public_link_attempts FROM papers WHERE paper_number=?",
                (args.paper_number,),
            ).fetchone()
            if paper is None:
                parser.error(f"unknown paper number: {args.paper_number}")
            if paper["public_page_url"]:
                payload = {"paper_number": paper["paper_number"], "status": "linked", "attempts": paper["public_link_attempts"]}
            else:
                attempts = paper["public_link_attempts"] + 1
                status = "manual_review" if attempts > 2 else "retry"
                connection.execute(
                    "UPDATE papers SET public_link_attempts=?, public_link_status=?, updated_at=CURRENT_TIMESTAMP WHERE paper_number=?",
                    (attempts, status, paper["paper_number"]),
                )
                connection.execute(
                    "INSERT INTO enrichment_attempts(paper_number,provider,matched_title,status,detail) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(paper_number,provider) DO UPDATE SET status=excluded.status,detail=excluded.detail,attempted_at=CURRENT_TIMESTAMP",
                    (paper["paper_number"], "public_page_search", None, status, args.detail[:500]),
                )
                payload = {"paper_number": paper["paper_number"], "status": status, "attempts": attempts}
            print(dump_json(payload))
    elif args.command == "enrich-exa-public":
        options = {
            "limit": args.limit,
            "delay": max(args.delay, 0.2),
            "abstract_only": args.abstract_only,
            "after_paper_number": args.after_paper_number,
            "only_untried": args.only_untried,
            "relaxed_author_retry": args.author_relaxed_retry,
        }
        if not args.until_exhausted:
            print(dump_json(enrich_exa_public(args.db, **options)))
        else:
            if not (args.only_untried or args.author_relaxed_retry):
                parser.error("--until-exhausted requires --only-untried or --author-relaxed-retry")
            total = {"batches": 0, "selected": 0, "linked": 0, "with_abstract": 0, "retry": 0, "manual_review": 0, "no_abstract": 0, "errors": 0}
            while True:
                result = enrich_exa_public(args.db, **options)
                if result["selected"] == 0:
                    break
                total["batches"] += 1
                for key in total:
                    if key != "batches":
                        total[key] += result.get(key, 0)
                if result["errors"]:
                    break
            print(dump_json(total))
    else:
        serve(args.db, args.port)


if __name__ == "__main__":
    main()
