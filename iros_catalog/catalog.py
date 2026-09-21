from __future__ import annotations

import json

from .db import connect
from .taxonomy import classify


def import_official_records(db_path: str, records: list[dict]) -> int:
    with connect(db_path) as connection:
        for record in records:
            paper_number = str(record["pn"])
            # One official index row currently has no RAS record ID. Keep a
            # blank value (rather than fabricating a link) for SQLite's
            # non-null canonical field and make its state queryable below.
            official_url = record.get("official_record_url") or ""
            infovaya_record = record.get("infovaya_record") or official_url
            authors = [name.strip() for name in record.get("authors", "").split(";") if name.strip()]
            keywords = [item.strip() for item in record.get("keywords", "").split(";") if item.strip()]
            connection.execute(
                """INSERT INTO papers (paper_number,title,official_keywords,author,keywords,session_name,session_type,day,time,room,affiliations,official_record_url,infovaya_record)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(paper_number) DO UPDATE SET title=excluded.title, official_keywords=excluded.official_keywords,
                   author=excluded.author, keywords=excluded.keywords,
                   session_name=excluded.session_name, session_type=excluded.session_type, day=excluded.day, time=excluded.time,
                   room=excluded.room, affiliations=excluded.affiliations, official_record_url=excluded.official_record_url, infovaya_record=excluded.infovaya_record,
                   pdf_status=CASE WHEN excluded.official_record_url = '' THEN 'official_record_unmapped' ELSE papers.pdf_status END,
                   updated_at=CURRENT_TIMESTAMP""",
                (paper_number, record["title"], record.get("keywords", ""), "; ".join(authors), json.dumps(keywords, ensure_ascii=False), record.get("session"), record.get("type"), record.get("day"), record.get("time_ampm") or record.get("time"), record.get("room"), record.get("affiliations", ""), official_url, infovaya_record),
            )
            connection.execute("DELETE FROM paper_authors WHERE paper_number=?", (paper_number,))
            for position, name in enumerate(authors):
                connection.execute("INSERT INTO authors(name) VALUES(?) ON CONFLICT(name) DO NOTHING", (name,))
                author_id = connection.execute("SELECT id FROM authors WHERE name=?", (name,)).fetchone()[0]
                connection.execute("INSERT INTO paper_authors(paper_number,author_id,position) VALUES(?,?,?)", (paper_number, author_id, position))
            connection.execute("DELETE FROM paper_topics WHERE paper_number=?", (paper_number,))
            for topic in classify(record.get("title"), record.get("keywords"), record.get("session")):
                connection.execute("INSERT INTO paper_topics(paper_number,topic) VALUES(?,?)", (paper_number, topic))
            _refresh_fts(connection, paper_number)
        return len(records)


def backfill_official_details(db_path: str, details: dict[str, dict[str, str]]) -> dict[str, int]:
    """Use IROS' official schedule dataset to set canonical abstracts/keywords."""
    counts = {"official_records": len(details), "matched": 0, "with_abstract": 0, "abstracts_updated": 0, "keywords_updated": 0, "not_in_catalog": 0}
    with connect(db_path) as connection:
        for paper_number, detail in details.items():
            paper = connection.execute(
                "SELECT paper_number,title,official_keywords,keywords,abstract,session_name FROM papers WHERE paper_number=?",
                (paper_number,),
            ).fetchone()
            if paper is None:
                counts["not_in_catalog"] += 1
                continue
            counts["matched"] += 1
            abstract = detail["abstract"]
            official_keywords = detail["keywords"]
            keyword_array = [item.strip() for item in official_keywords.split(";") if item.strip()]
            if abstract:
                counts["with_abstract"] += 1
                if abstract != (paper["abstract"] or ""):
                    counts["abstracts_updated"] += 1
            if official_keywords and (official_keywords != (paper["official_keywords"] or "") or json.dumps(keyword_array, ensure_ascii=False) != (paper["keywords"] or "[]")):
                counts["keywords_updated"] += 1
            connection.execute(
                "UPDATE papers SET "
                "abstract=CASE WHEN ? <> '' THEN ? ELSE abstract END, "
                "abstract_source=CASE WHEN ? <> '' THEN 'official_iros_schedule' ELSE abstract_source END, "
                "official_keywords=CASE WHEN ? <> '' THEN ? ELSE official_keywords END, "
                "keywords=CASE WHEN ? <> '' THEN ? ELSE keywords END, "
                "updated_at=CURRENT_TIMESTAMP WHERE paper_number=?",
                (abstract, abstract, abstract, official_keywords, official_keywords, official_keywords, json.dumps(keyword_array, ensure_ascii=False), paper_number),
            )
            if official_keywords:
                connection.execute("DELETE FROM paper_topics WHERE paper_number=?", (paper_number,))
                for topic in classify(paper["title"], official_keywords, paper["session_name"] or ""):
                    connection.execute("INSERT INTO paper_topics(paper_number,topic) VALUES(?,?)", (paper_number, topic))
            _refresh_fts(connection, paper_number)
    return counts


def _refresh_fts(connection, paper_number: str) -> None:
    paper = connection.execute("SELECT title, official_keywords, abstract FROM papers WHERE paper_number=?", (paper_number,)).fetchone()
    authors = "; ".join(row[0] for row in connection.execute("SELECT a.name FROM authors a JOIN paper_authors pa ON pa.author_id=a.id WHERE pa.paper_number=? ORDER BY pa.position", (paper_number,)))
    connection.execute("DELETE FROM papers_fts WHERE paper_number=?", (paper_number,))
    connection.execute("INSERT INTO papers_fts(paper_number,title,authors,keywords,abstract) VALUES(?,?,?,?,?)", (paper_number, paper["title"], authors, paper["official_keywords"], paper["abstract"] or ""))


def refresh_fts_for_paper(db_path: str, paper_number: str) -> None:
    with connect(db_path) as connection:
        _refresh_fts(connection, paper_number)
