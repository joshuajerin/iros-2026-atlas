"""Derived Atlas data: normalized keywords, conference-presence entities, and scores.

The official catalog remains the source of truth. This module only creates derived
tables and deliberately never fetches scholarly providers at request time.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from typing import Any

from . import ATLAS_SCORE_VERSION
from .db import connect, paper_payload


TOPIC_LABELS = {
    "ai_and_foundation_models": "AI & foundation models",
    "planning_and_decision_making": "Planning & decision making",
    "learning_and_reinforcement_learning": "Learning & reinforcement learning",
    "manipulation_and_grasping": "Manipulation & grasping",
    "perception_and_vision": "Perception & vision",
    "navigation_and_mapping": "Navigation & mapping",
    "control_and_optimization": "Control & optimization",
    "human_robot_interaction": "Human-robot interaction",
    "medical_and_healthcare_robotics": "Medical & healthcare robotics",
    "aerial_and_field_robotics": "Aerial & field robotics",
    "locomotion_and_legged_robots": "Locomotion & legged robots",
    "soft_and_bioinspired_robotics": "Soft & bio-inspired robotics",
    "safety_and_reliability": "Safety & reliability",
    "other_robotics": "Other robotics",
}


def _slug(value: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _title(value: str) -> str:
    return " ".join(value.strip().split())


def _keywords(raw: str | None) -> list[str]:
    seen: dict[str, str] = {}
    for item in (raw or "").split(";"):
        label = _title(item)
        key = _slug(label)
        if key and key not in seen:
            seen[key] = label
    return list(seen.values())


def _affiliations(raw: str | None) -> list[str]:
    seen: dict[str, str] = {}
    for item in (raw or "").split(";"):
        label = _title(item)
        key = _slug(label)
        if len(key) > 2 and key not in seen:
            seen[key] = label
    return list(seen.values())


def _primary_topic(connection: sqlite3.Connection, paper_number: str) -> str:
    row = connection.execute(
        "SELECT topic FROM paper_topics WHERE paper_number=? ORDER BY topic LIMIT 1", (paper_number,)
    ).fetchone()
    return row[0] if row else "other_robotics"


def _score(paper: sqlite3.Row) -> tuple[dict[str, float], list[dict[str, str]], float, float]:
    """Return components, evidence, score, and confidence without inventing impact."""
    text = " ".join(filter(None, [paper["session_type"], paper["session_name"]])).casefold()
    award = 100.0 if "award" in text else 0.0
    # Citation, influential-citation, and verified-code metrics are neutral until
    # an exact external identifier is imported. They are never treated as zero.
    citations = 50.0
    influential = 50.0
    artifact = 80.0 if paper["pdf_url"] else 65.0 if paper["public_page_url"] else 50.0
    confirmations = sum(
        value is not None and value != ""
        for value in (paper["doi"], paper["scholarly_url"], paper["public_page_url"], paper["pdf_url"], paper["abstract"])
    )
    confirmation = min(100.0, confirmations * 20.0)
    components = {
        "official_recognition": award,
        "citation_velocity": citations,
        "influential_citations": influential,
        "reproducibility": artifact,
        "scholarly_confirmation": confirmation,
    }
    score = round(
        0.30 * award + 0.30 * citations + 0.20 * influential + 0.15 * artifact + 0.05 * confirmation,
        2,
    )
    confidence = round(min(0.95, 0.55 + confirmations * 0.09), 2)
    evidence = [{"source": "official_iros", "url": paper["official_record_url"], "status": "verified"}]
    for source, value in (("crossref", paper["doi"]), ("openalex", paper["scholarly_url"]), ("public_page", paper["public_page_url"]), ("open_access", paper["pdf_url"])):
        if value:
            evidence.append({"source": source, "url": value, "status": "verified"})
    if not paper["doi"] and not paper["scholarly_url"]:
        evidence.append({"source": "citation_metrics", "status": "neutral_prior"})
    return components, evidence, score, confidence


def build_atlas(db_path: str | Path) -> dict[str, int]:
    """Rebuild all Atlas-derived tables from the canonical SQLite catalog."""
    built_at = datetime.now(UTC).isoformat()
    with connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS atlas_keywords (
              slug TEXT PRIMARY KEY, label TEXT NOT NULL, topic TEXT NOT NULL, paper_count INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS atlas_paper_keywords (
              paper_number TEXT NOT NULL, keyword_slug TEXT NOT NULL, raw_value TEXT NOT NULL,
              PRIMARY KEY(paper_number, keyword_slug)
            );
            CREATE TABLE IF NOT EXISTS atlas_institutions (
              slug TEXT PRIMARY KEY, name TEXT NOT NULL, paper_count INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS atlas_paper_institutions (
              paper_number TEXT NOT NULL, institution_slug TEXT NOT NULL, raw_value TEXT NOT NULL,
              PRIMARY KEY(paper_number, institution_slug)
            );
            CREATE TABLE IF NOT EXISTS atlas_scores (
              paper_number TEXT PRIMARY KEY, score REAL NOT NULL, confidence REAL NOT NULL, eligible INTEGER NOT NULL,
              rank INTEGER, topic_ranks_json TEXT NOT NULL, components_json TEXT NOT NULL, evidence_json TEXT NOT NULL,
              score_version TEXT NOT NULL, computed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS atlas_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            DELETE FROM atlas_keywords;
            DELETE FROM atlas_paper_keywords;
            DELETE FROM atlas_institutions;
            DELETE FROM atlas_paper_institutions;
            DELETE FROM atlas_scores;
            """
        )
        keyword_rows: dict[str, dict[str, Any]] = {}
        institution_rows: dict[str, dict[str, Any]] = {}
        topic_papers: defaultdict[str, list[tuple[str, float, float]]] = defaultdict(list)
        scores: list[tuple[str, float, float, bool, dict[str, float], list[dict[str, str]]]] = []
        papers = connection.execute("SELECT * FROM papers ORDER BY CAST(paper_number AS INTEGER)").fetchall()
        for paper in papers:
            number = paper["paper_number"]
            topic = _primary_topic(connection, number)
            for label in _keywords(paper["official_keywords"]):
                slug = _slug(label)
                row = keyword_rows.setdefault(slug, {"label": label, "topic": topic, "papers": set()})
                row["papers"].add(number)
                connection.execute(
                    "INSERT INTO atlas_paper_keywords(paper_number,keyword_slug,raw_value) VALUES(?,?,?)",
                    (number, slug, label),
                )
            for label in _affiliations(paper["affiliations"]):
                slug = _slug(label)
                row = institution_rows.setdefault(slug, {"name": label, "papers": set()})
                row["papers"].add(number)
                connection.execute(
                    "INSERT INTO atlas_paper_institutions(paper_number,institution_slug,raw_value) VALUES(?,?,?)",
                    (number, slug, label),
                )
            components, evidence, score, confidence = _score(paper)
            eligible = confidence >= 0.60
            scores.append((number, score, confidence, eligible, components, evidence))
            for row in connection.execute("SELECT topic FROM paper_topics WHERE paper_number=?", (number,)):
                topic_papers[row[0]].append((number, score, confidence))
        for slug, row in keyword_rows.items():
            connection.execute(
                "INSERT INTO atlas_keywords(slug,label,topic,paper_count) VALUES(?,?,?,?)",
                (slug, row["label"], row["topic"], len(row["papers"])),
            )
        for slug, row in institution_rows.items():
            connection.execute(
                "INSERT INTO atlas_institutions(slug,name,paper_count) VALUES(?,?,?)",
                (slug, row["name"], len(row["papers"])),
            )
        global_ranks = {
            number: index
            for index, (number, *_rest) in enumerate(
                sorted((item for item in scores if item[3]), key=lambda item: (-item[1], -item[2], int(item[0]))), start=1
            )
        }
        topic_ranks: defaultdict[str, dict[str, int]] = defaultdict(dict)
        for topic, values in topic_papers.items():
            eligible = [item for item in values if item[2] >= 0.60]
            for index, (number, _score_value, _confidence) in enumerate(
                sorted(eligible, key=lambda item: (-item[1], -item[2], int(item[0]))), start=1
            ):
                topic_ranks[number][topic] = index
        for number, score, confidence, eligible, components, evidence in scores:
            connection.execute(
                "INSERT INTO atlas_scores VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    number, score, confidence, int(eligible), global_ranks.get(number),
                    json.dumps(topic_ranks[number]), json.dumps(components), json.dumps(evidence),
                    ATLAS_SCORE_VERSION, built_at,
                ),
            )
        metadata = {
            "built_at": built_at,
            "score_version": ATLAS_SCORE_VERSION,
            "papers": str(len(papers)),
            "keywords": str(len(keyword_rows)),
            "institutions": str(len(institution_rows)),
        }
        for key, value in metadata.items():
            connection.execute("INSERT OR REPLACE INTO atlas_metadata(key,value) VALUES(?,?)", (key, value))
    return {"papers": len(papers), "keywords": len(keyword_rows), "institutions": len(institution_rows)}


def _metadata(connection: sqlite3.Connection) -> dict[str, str]:
    rows = connection.execute("SELECT key,value FROM atlas_metadata").fetchall()
    if not rows:
        raise RuntimeError("Atlas data is not built. Run `python -m iros_catalog --db data/iros.sqlite atlas-build`.")
    return {row["key"]: row["value"] for row in rows}


def _score_payload(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "score": row["score"], "confidence": row["confidence"], "eligible": bool(row["eligible"]), "rank": row["rank"],
        "topic_ranks": json.loads(row["topic_ranks_json"]), "components": json.loads(row["components_json"]),
        "evidence": json.loads(row["evidence_json"]), "version": row["score_version"], "computed_at": row["computed_at"],
    }


def _paper_summary(connection: sqlite3.Connection, paper: sqlite3.Row) -> dict[str, Any]:
    payload = paper_payload(connection, paper)
    payload["keywords"] = [row[0] for row in connection.execute(
        "SELECT raw_value FROM atlas_paper_keywords WHERE paper_number=? ORDER BY raw_value", (paper["paper_number"],)
    )]
    payload["score"] = _score_payload(connection.execute("SELECT * FROM atlas_scores WHERE paper_number=?", (paper["paper_number"],)).fetchone())
    for key in ("abstract", "affiliations", "time", "room", "session_name", "official_record_url", "doi", "pdf_status", "pdf_source"):
        payload.setdefault(key, paper[key])
    return payload


def overview(db_path: str | Path) -> dict[str, Any]:
    with connect(db_path) as connection:
        meta = _metadata(connection)
        topics = [dict(row) for row in connection.execute(
            "SELECT pt.topic, COUNT(DISTINCT pt.paper_number) AS paper_count FROM paper_topics pt GROUP BY pt.topic ORDER BY paper_count DESC"
        )]
        ranked = connection.execute(
            "SELECT p.* FROM papers p JOIN atlas_scores s USING(paper_number) WHERE s.eligible=1 ORDER BY s.rank LIMIT 6"
        ).fetchall()
        return {
            "stats": {
                "papers": int(meta["papers"]), "keywords": int(meta["keywords"]), "institutions": int(meta["institutions"]),
                "authors": connection.execute("SELECT count(*) FROM authors").fetchone()[0],
            },
            "topics": [{**item, "label": TOPIC_LABELS.get(item["topic"], item["topic"].replace("_", " ").title())} for item in topics],
            "top_papers": [_paper_summary(connection, paper) for paper in ranked],
            "built_at": meta["built_at"], "score_version": meta["score_version"],
        }


def search_papers(db_path: str | Path, query: str | None = None, topic: str | None = None, keyword: str | None = None, institution: str | None = None, sort: str = "atlas_score", limit: int = 30, offset: int = 0) -> dict[str, Any]:
    with connect(db_path) as connection:
        _metadata(connection)
        conditions, values = [], []
        if query:
            tokens = re.findall(r"[\w-]+", query.casefold())[:12]
            if tokens:
                conditions.append("p.paper_number IN (SELECT paper_number FROM papers_fts WHERE papers_fts MATCH ?)")
                values.append(" AND ".join(f"{token}*" for token in tokens))
        if topic:
            conditions.append("EXISTS (SELECT 1 FROM paper_topics pt WHERE pt.paper_number=p.paper_number AND pt.topic=?)")
            values.append(topic)
        if keyword:
            conditions.append("EXISTS (SELECT 1 FROM atlas_paper_keywords pk WHERE pk.paper_number=p.paper_number AND pk.keyword_slug=?)")
            values.append(_slug(keyword))
        if institution:
            conditions.append("EXISTS (SELECT 1 FROM atlas_paper_institutions pi WHERE pi.paper_number=p.paper_number AND pi.institution_slug=?)")
            values.append(_slug(institution))
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        order = "s.score DESC, s.confidence DESC, CAST(p.paper_number AS INTEGER)" if sort == "atlas_score" else "CAST(p.paper_number AS INTEGER)"
        total = connection.execute(f"SELECT count(*) FROM papers p JOIN atlas_scores s USING(paper_number){where}", values).fetchone()[0]
        rows = connection.execute(
            f"SELECT p.* FROM papers p JOIN atlas_scores s USING(paper_number){where} ORDER BY {order} LIMIT ? OFFSET ?", [*values, limit, offset]
        ).fetchall()
        return {"count": total, "limit": limit, "offset": offset, "papers": [_paper_summary(connection, row) for row in rows]}


def paper_detail(db_path: str | Path, paper_number: str) -> dict[str, Any] | None:
    with connect(db_path) as connection:
        _metadata(connection)
        paper = connection.execute("SELECT * FROM papers WHERE paper_number=?", (paper_number,)).fetchone()
        if paper is None:
            return None
        payload = _paper_summary(connection, paper)
        payload["affiliations"] = _affiliations(paper["affiliations"])
        related = connection.execute(
            """SELECT p.* FROM papers p JOIN atlas_scores s USING(paper_number)
               WHERE p.paper_number != ? AND EXISTS (
                 SELECT 1 FROM paper_topics a JOIN paper_topics b ON a.topic=b.topic
                 WHERE a.paper_number=? AND b.paper_number=p.paper_number
               ) ORDER BY s.score DESC LIMIT 6""",
            (paper_number, paper_number),
        ).fetchall()
        payload["related_papers"] = [_paper_summary(connection, row) for row in related]
        return payload


def keyword_network(db_path: str | Path, limit: int = 80) -> dict[str, Any]:
    with connect(db_path) as connection:
        _metadata(connection)
        rows = connection.execute("SELECT * FROM atlas_keywords ORDER BY paper_count DESC, label LIMIT ?", (limit,)).fetchall()
        selected = {row["slug"] for row in rows}
        paper_words: defaultdict[str, list[str]] = defaultdict(list)
        for row in connection.execute("SELECT paper_number,keyword_slug FROM atlas_paper_keywords"):
            if row["keyword_slug"] in selected:
                paper_words[row["paper_number"]].append(row["keyword_slug"])
        edges: Counter[tuple[str, str]] = Counter()
        for words in paper_words.values():
            for pair in combinations(sorted(set(words)), 2):
                edges[pair] += 1
        return {
            "nodes": [{"id": row["slug"], "label": row["label"], "count": row["paper_count"], "topic": row["topic"]} for row in rows],
            "edges": [{"source": source, "target": target, "count": count} for (source, target), count in edges.most_common(180) if count >= 2],
        }


def rankings(db_path: str | Path, kind: str, limit: int = 30, topic: str | None = None) -> dict[str, Any]:
    with connect(db_path) as connection:
        _metadata(connection)
        if kind == "papers":
            conditions, values = ["s.eligible=1"], []
            if topic:
                conditions.append("EXISTS (SELECT 1 FROM paper_topics pt WHERE pt.paper_number=p.paper_number AND pt.topic=?)")
                values.append(topic)
            rows = connection.execute(
                "SELECT p.* FROM papers p JOIN atlas_scores s USING(paper_number) WHERE " + " AND ".join(conditions) + " ORDER BY s.score DESC, s.confidence DESC LIMIT ?",
                [*values, limit],
            ).fetchall()
            return {"kind": kind, "topic": topic, "items": [_paper_summary(connection, row) for row in rows]}
        if kind == "institutions":
            rows = connection.execute(
                """SELECT i.slug,i.name,i.paper_count, AVG(s.score) AS avg_score, COUNT(DISTINCT pt.topic) AS topic_breadth
                   FROM atlas_institutions i JOIN atlas_paper_institutions pi ON pi.institution_slug=i.slug
                   JOIN atlas_scores s ON s.paper_number=pi.paper_number
                   LEFT JOIN paper_topics pt ON pt.paper_number=pi.paper_number
                   GROUP BY i.slug ORDER BY (AVG(s.score) * sqrt(i.paper_count)) DESC LIMIT ?""", (limit,)
            ).fetchall()
            items = []
            for row in rows:
                top_topics = [item[0] for item in connection.execute(
                    "SELECT pt.topic FROM atlas_paper_institutions pi JOIN paper_topics pt ON pt.paper_number=pi.paper_number WHERE pi.institution_slug=? GROUP BY pt.topic ORDER BY count(*) DESC LIMIT 3", (row["slug"],)
                )]
                items.append({"id": row["slug"], "name": row["name"], "paper_count": row["paper_count"], "score": round(row["avg_score"] * math.sqrt(row["paper_count"]), 2), "topic_breadth": row["topic_breadth"], "top_topics": top_topics})
            return {"kind": kind, "items": items, "methodology": "IROS 2026 research presence, not institutional prestige."}
        if kind == "researchers":
            rows = connection.execute(
                """SELECT a.id,a.name,COUNT(pa.paper_number) AS paper_count, SUM(s.score * 1.0 / counts.n) AS score,
                          COUNT(DISTINCT pt.topic) AS topic_breadth
                   FROM authors a JOIN paper_authors pa ON pa.author_id=a.id JOIN atlas_scores s ON s.paper_number=pa.paper_number
                   JOIN (SELECT paper_number,COUNT(*) AS n FROM paper_authors GROUP BY paper_number) counts ON counts.paper_number=pa.paper_number
                   LEFT JOIN paper_topics pt ON pt.paper_number=pa.paper_number
                   GROUP BY a.id ORDER BY score DESC LIMIT ?""", (limit,)
            ).fetchall()
            return {"kind": kind, "items": [{"id": row["id"], "name": row["name"], "paper_count": row["paper_count"], "score": round(row["score"], 2), "topic_breadth": row["topic_breadth"], "identity_confidence": "source_name_only"} for row in rows], "methodology": "Fractional Atlas Score across source names; ambiguous identities are not merged."}
        raise ValueError("kind must be papers, institutions, or researchers")


def topic_detail(db_path: str | Path, topic: str) -> dict[str, Any] | None:
    with connect(db_path) as connection:
        _metadata(connection)
        count = connection.execute("SELECT count(*) FROM paper_topics WHERE topic=?", (topic,)).fetchone()[0]
        if not count:
            return None
        words = [dict(row) for row in connection.execute(
            """SELECT k.slug,k.label,k.paper_count FROM atlas_keywords k JOIN atlas_paper_keywords pk ON pk.keyword_slug=k.slug
               JOIN paper_topics pt ON pt.paper_number=pk.paper_number WHERE pt.topic=? GROUP BY k.slug ORDER BY count(DISTINCT pk.paper_number) DESC LIMIT 16""", (topic,)
        )]
        return {"slug": topic, "label": TOPIC_LABELS.get(topic, topic.replace("_", " ").title()), "paper_count": count, "keywords": words, "rankings": rankings(db_path, "papers", 12, topic)["items"]}


def institution_profile(db_path: str | Path, institution: str) -> dict[str, Any] | None:
    """Return a conference-presence profile; aliases remain explicitly source-derived."""
    with connect(db_path) as connection:
        _metadata(connection)
        row = connection.execute("SELECT * FROM atlas_institutions WHERE slug=?", (_slug(institution),)).fetchone()
        if row is None:
            return None
        papers = connection.execute(
            """SELECT p.* FROM papers p JOIN atlas_paper_institutions pi USING(paper_number)
               JOIN atlas_scores s USING(paper_number) WHERE pi.institution_slug=?
               ORDER BY s.score DESC, s.confidence DESC LIMIT 30""",
            (row["slug"],),
        ).fetchall()
        topics = [dict(item) for item in connection.execute(
            """SELECT pt.topic, COUNT(DISTINCT pi.paper_number) AS paper_count
               FROM atlas_paper_institutions pi JOIN paper_topics pt USING(paper_number)
               WHERE pi.institution_slug=? GROUP BY pt.topic ORDER BY paper_count DESC""",
            (row["slug"],),
        )]
        return {"id": row["slug"], "name": row["name"], "paper_count": row["paper_count"], "identity": "source-affiliation canonicalization; ROR not yet imported", "topics": topics, "papers": [_paper_summary(connection, paper) for paper in papers]}


def researcher_profile(db_path: str | Path, researcher_id: str) -> dict[str, Any] | None:
    """Return a source-name profile without unsafe cross-record identity merging."""
    with connect(db_path) as connection:
        _metadata(connection)
        author = connection.execute("SELECT * FROM authors WHERE id=?", (researcher_id,)).fetchone()
        if author is None:
            return None
        papers = connection.execute(
            """SELECT p.* FROM papers p JOIN paper_authors pa USING(paper_number)
               JOIN atlas_scores s USING(paper_number) WHERE pa.author_id=?
               ORDER BY s.score DESC, s.confidence DESC LIMIT 30""",
            (researcher_id,),
        ).fetchall()
        topics = [dict(item) for item in connection.execute(
            """SELECT pt.topic,COUNT(DISTINCT pa.paper_number) AS paper_count FROM paper_authors pa
               JOIN paper_topics pt USING(paper_number) WHERE pa.author_id=?
               GROUP BY pt.topic ORDER BY paper_count DESC""",
            (researcher_id,),
        )]
        return {"id": author["id"], "name": author["name"], "identity_confidence": "source_name_only", "topics": topics, "papers": [_paper_summary(connection, paper) for paper in papers]}


def methodology(db_path: str | Path) -> dict[str, Any]:
    with connect(db_path) as connection:
        meta = _metadata(connection)
    return {
        "version": meta["score_version"], "computed_at": meta["built_at"], "name": "Atlas Score",
        "weights": {"official_recognition": 0.30, "citation_velocity": 0.30, "influential_citations": 0.20, "reproducibility": 0.15, "scholarly_confirmation": 0.05},
        "rules": [
            "Only exact external identifiers may replace neutral citation priors.",
            "Missing external metrics are neutral, never zero.",
            "Records below 0.60 evidence confidence remain searchable but are excluded from ranked lists.",
            "Institution results describe IROS 2026 research presence, not prestige.",
        ],
    }
