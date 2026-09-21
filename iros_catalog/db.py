from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS papers (
  paper_number TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  official_keywords TEXT NOT NULL DEFAULT '',
  author TEXT,
  keywords TEXT NOT NULL DEFAULT '[]',
  session_name TEXT,
  session_type TEXT,
  day TEXT,
  time TEXT,
  room TEXT,
  affiliations TEXT,
  official_record_url TEXT NOT NULL,
  infovaya_record TEXT,
  abstract TEXT,
  abstract_source TEXT,
  doi TEXT,
  ieee_url TEXT,
  public_page_url TEXT,
  public_page_source TEXT,
  public_link_attempts INTEGER NOT NULL DEFAULT 0,
  public_link_status TEXT NOT NULL DEFAULT 'pending',
  scholarly_url TEXT,
  arxiv_id TEXT,
  pdf_url TEXT,
  pdf_source TEXT,
  pdf_status TEXT NOT NULL DEFAULT 'official_record_only',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS authors (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS paper_authors (
  paper_number TEXT NOT NULL REFERENCES papers(paper_number) ON DELETE CASCADE,
  author_id INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  PRIMARY KEY (paper_number, author_id)
);
CREATE TABLE IF NOT EXISTS paper_topics (
  paper_number TEXT NOT NULL REFERENCES papers(paper_number) ON DELETE CASCADE,
  topic TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'taxonomy_v1',
  PRIMARY KEY (paper_number, topic)
);
CREATE TABLE IF NOT EXISTS enrichment_attempts (
  paper_number TEXT NOT NULL REFERENCES papers(paper_number) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  matched_title TEXT,
  status TEXT NOT NULL,
  detail TEXT,
  attempted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (paper_number, provider)
);
CREATE TABLE IF NOT EXISTS arxiv_harvest_state (
  category TEXT PRIMARY KEY,
  next_start INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
  paper_number UNINDEXED, title, authors, keywords, abstract
);
CREATE INDEX IF NOT EXISTS idx_paper_topics_topic ON paper_topics(topic);
CREATE INDEX IF NOT EXISTS idx_papers_pdf_status ON papers(pdf_status);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(path: str | Path) -> None:
    with connect(path) as connection:
        connection.executescript(SCHEMA)
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(papers)")}
        if "arxiv_id" not in columns:
            connection.execute("ALTER TABLE papers ADD COLUMN arxiv_id TEXT")
        if "doi" not in columns:
            connection.execute("ALTER TABLE papers ADD COLUMN doi TEXT")
        if "ieee_url" not in columns:
            connection.execute("ALTER TABLE papers ADD COLUMN ieee_url TEXT")
        if "infovaya_record" not in columns:
            connection.execute("ALTER TABLE papers ADD COLUMN infovaya_record TEXT")
        if "author" not in columns:
            connection.execute("ALTER TABLE papers ADD COLUMN author TEXT")
        if "keywords" not in columns:
            connection.execute("ALTER TABLE papers ADD COLUMN keywords TEXT NOT NULL DEFAULT '[]'")
        if "public_page_url" not in columns:
            connection.execute("ALTER TABLE papers ADD COLUMN public_page_url TEXT")
        if "public_page_source" not in columns:
            connection.execute("ALTER TABLE papers ADD COLUMN public_page_source TEXT")
        if "public_link_attempts" not in columns:
            connection.execute("ALTER TABLE papers ADD COLUMN public_link_attempts INTEGER NOT NULL DEFAULT 0")
        if "public_link_status" not in columns:
            connection.execute("ALTER TABLE papers ADD COLUMN public_link_status TEXT NOT NULL DEFAULT 'pending'")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_papers_public_link_queue "
            "ON papers(public_link_status, public_link_attempts)"
        )
        # Preserve the first manually verified IEEE link that predated the
        # dedicated field. It remains a landing-page link, never a PDF link.
        connection.execute("UPDATE papers SET ieee_url=scholarly_url WHERE ieee_url IS NULL AND scholarly_url LIKE 'https://ieeexplore.ieee.org/document/%'")
        # A public page is a reader-facing metadata/abstract page, never a PDF.
        # Existing IEEE and arXiv matches can therefore seed the queue safely.
        connection.execute(
            "UPDATE papers SET public_page_url=COALESCE(public_page_url, ieee_url), "
            "public_page_source=CASE WHEN public_page_url IS NULL AND ieee_url IS NOT NULL THEN 'ieee' ELSE public_page_source END "
            "WHERE ieee_url IS NOT NULL"
        )
        connection.execute(
            "UPDATE papers SET public_page_url=COALESCE(public_page_url, 'https://arxiv.org/abs/' || arxiv_id), "
            "public_page_source=CASE WHEN public_page_url IS NULL THEN 'arxiv' ELSE public_page_source END "
            "WHERE arxiv_id IS NOT NULL"
        )
        connection.execute(
            "UPDATE papers SET public_link_status=CASE WHEN public_page_url IS NOT NULL THEN 'linked' "
            "WHEN public_link_attempts > 2 THEN 'manual_review' ELSE public_link_status END"
        )
        # IROS' official "Files" control labels these RAS event records as
        # Infovaya records. They are landing pages, not PDF URLs.
        connection.execute(
            "UPDATE papers SET infovaya_record=COALESCE(NULLIF(infovaya_record,''), NULLIF(official_record_url,''))"
        )
        # Denormalized query-friendly mirrors. The normalized author and topic
        # tables remain canonical; these columns make row-level API/SQLite
        # consumption straightforward.
        papers = list(connection.execute("SELECT paper_number,official_keywords,author,keywords FROM papers"))
        for paper in papers:
            authors = [row[0] for row in connection.execute(
                "SELECT a.name FROM authors a JOIN paper_authors pa ON pa.author_id=a.id WHERE pa.paper_number=? ORDER BY pa.position",
                (paper["paper_number"],),
            )]
            author_text = "; ".join(authors)
            keyword_array = [item.strip() for item in paper["official_keywords"].split(";") if item.strip()]
            connection.execute(
                "UPDATE papers SET author=CASE WHEN author IS NULL OR trim(author)='' THEN ? ELSE author END, "
                "keywords=CASE WHEN keywords IS NULL OR trim(keywords)='' OR keywords='[]' THEN ? ELSE keywords END "
                "WHERE paper_number=?",
                (author_text, json.dumps(keyword_array, ensure_ascii=False), paper["paper_number"]),
            )
        # v0.1 initially used a contentless FTS table, which cannot retain
        # column text for ordinary DELETE/INSERT refreshes. The index is
        # derived data, so this migration is safe and self-contained.
        definition = connection.execute("SELECT sql FROM sqlite_master WHERE name='papers_fts'").fetchone()[0]
        if "content=''" in definition:
            connection.execute("DROP TABLE papers_fts")
            connection.execute("CREATE VIRTUAL TABLE papers_fts USING fts5(paper_number UNINDEXED, title, authors, keywords, abstract)")
            _rebuild_fts(connection)


def _rebuild_fts(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM papers_fts")
    for paper in connection.execute("SELECT paper_number,title,official_keywords,abstract FROM papers"):
        authors = "; ".join(row[0] for row in connection.execute("SELECT a.name FROM authors a JOIN paper_authors pa ON pa.author_id=a.id WHERE pa.paper_number=? ORDER BY pa.position", (paper["paper_number"],)))
        connection.execute("INSERT INTO papers_fts(paper_number,title,authors,keywords,abstract) VALUES(?,?,?,?,?)", (paper["paper_number"], paper["title"], authors, paper["official_keywords"], paper["abstract"] or ""))


def paper_payload(connection: sqlite3.Connection, row: sqlite3.Row) -> dict:
    paper = dict(row)
    paper["authors"] = [r["name"] for r in connection.execute(
        "SELECT a.name FROM authors a JOIN paper_authors pa ON pa.author_id=a.id WHERE pa.paper_number=? ORDER BY pa.position",
        (paper["paper_number"],),
    )]
    paper["topics"] = [r["topic"] for r in connection.execute(
        "SELECT topic FROM paper_topics WHERE paper_number=? ORDER BY topic", (paper["paper_number"],)
    )]
    return paper


def dump_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)
