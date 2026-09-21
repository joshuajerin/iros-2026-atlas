"""Public scholarly-index enrichment. Never downloads restricted conference files."""

from __future__ import annotations

import json
import os
import re
import time
from html import unescape
from urllib.parse import quote
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from .catalog import refresh_fts_for_paper
from .db import connect


EXA_SEARCH_URL = "https://api.exa.ai/search"
_PUBLIC_PAGE_SOURCES = ("ieee", "arxiv", "github_pages", "institutional_repository")


def _get_json(url: str) -> dict:
    request = Request(url, headers={"User-Agent": "IROS-paper-catalog/0.1 (mailto:research@example.invalid)"})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def _post_json(url: str, payload: dict, headers: dict[str, str]) -> dict:
    request = Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def _similar(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a.casefold(), b.casefold()).ratio()


def _openalex_abstract(inverted: dict | None) -> str | None:
    if not inverted:
        return None
    words = sorted(((position, word) for word, positions in inverted.items() for position in positions))
    return " ".join(word for _, word in words)


def _plain_abstract(value: str | None) -> str | None:
    """Turn Crossref's optional JATS/HTML abstract into searchable text."""
    if not value:
        return None
    text = re.sub(r"<[^>]+>", " ", value)
    text = " ".join(unescape(text).split())
    return text or None


def _crossref_candidate(items: list[dict], official_title: str) -> tuple[str, str, str | None] | None:
    """Return a verified IEEE landing page only for an exact title match."""
    wanted = _normalise_title(official_title)
    for item in items:
        titles = item.get("title") or []
        candidate_title = titles[0] if titles else ""
        primary_url = ((item.get("resource") or {}).get("primary") or {}).get("URL")
        if _normalise_title(candidate_title) != wanted:
            continue
        if not isinstance(primary_url, str) or not primary_url.startswith("https://ieeexplore.ieee.org/document/"):
            continue
        doi = item.get("DOI")
        if not isinstance(doi, str) or not doi:
            continue
        return doi.casefold(), primary_url.rstrip("/"), _plain_abstract(item.get("abstract"))
    return None


def _openalex_exact_abstract(title: str) -> str | None:
    """Fetch an abstract only when OpenAlex also returns the exact title."""
    result = _get_json(
        "https://api.openalex.org/works?search="
        + quote(title)
        + "&per-page=3&select=display_name,abstract_inverted_index"
    )
    wanted = _normalise_title(title)
    for item in result.get("results") or []:
        if _normalise_title(item.get("display_name", "")) == wanted:
            return _openalex_abstract(item.get("abstract_inverted_index"))
    return None


def enrich_crossref_ieee(db_path: str, limit: int | None = None, delay: float = 0.5) -> dict[str, int]:
    """Attach Crossref-verified IEEE Xplore record pages and abstract text.

    This intentionally does not read or populate ``pdf_url``. IEEE links are
    publisher landing pages, while abstracts can be supplied by Crossref's
    metadata record when present. Exact normalized-title matching prevents a
    similarly named work from being attached to an IROS record.
    """
    with connect(db_path) as connection:
        sql = "SELECT paper_number,title FROM papers WHERE ieee_url IS NULL OR doi IS NULL ORDER BY paper_number"
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = connection.execute(sql).fetchall()
    counts = {"matched": 0, "with_abstract": 0, "no_match": 0, "errors": 0}
    for row in rows:
        paper_number, title = row["paper_number"], row["title"]
        try:
            url = "https://api.crossref.org/works?query.title=" + quote(title) + "&rows=3&select=title,DOI,resource,abstract"
            result = _get_json(url)
            candidate = _crossref_candidate((result.get("message") or {}).get("items") or [], title)
            if not candidate:
                _attempt(db_path, paper_number, "no_match", None, "method=normalized_exact; source=crossref", provider="crossref_ieee")
                counts["no_match"] += 1
            else:
                doi, ieee_url, abstract = candidate
                abstract_source = "crossref" if abstract else None
                if not abstract:
                    abstract = _openalex_exact_abstract(title)
                    abstract_source = "openalex" if abstract else None
                with connect(db_path) as connection:
                    connection.execute(
                        "UPDATE papers SET doi=?, ieee_url=?, abstract=COALESCE(abstract,?), abstract_source=CASE WHEN abstract IS NULL AND ? IS NOT NULL THEN ? ELSE abstract_source END, updated_at=CURRENT_TIMESTAMP WHERE paper_number=?",
                        (doi, ieee_url, abstract, abstract, abstract_source, paper_number),
                    )
                _attempt(db_path, paper_number, "matched", title, f"doi={doi}; landing_page={ieee_url}", provider="crossref_ieee")
                refresh_fts_for_paper(db_path, paper_number)
                counts["matched"] += 1
                counts["with_abstract"] += int(abstract is not None)
        except Exception as error:
            _attempt(db_path, paper_number, "error", None, str(error)[:500], provider="crossref_ieee")
            counts["errors"] += 1
        time.sleep(delay)
    return counts


def enrich_openalex(db_path: str, limit: int | None = None, delay: float = 0.8) -> dict[str, int]:
    """Populate abstracts and direct OA PDFs from OpenAlex only when title match is strong."""
    with connect(db_path) as connection:
        sql = "SELECT paper_number,title FROM papers WHERE paper_number NOT IN (SELECT paper_number FROM enrichment_attempts WHERE provider='openalex' AND status IN ('matched','no_match')) ORDER BY paper_number"
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = connection.execute(sql).fetchall()
    counts = {"matched": 0, "no_match": 0, "errors": 0}
    for row in rows:
        paper_number, title = row["paper_number"], row["title"]
        try:
            result = _get_json("https://api.openalex.org/works?search=" + quote(title) + "&per-page=1&select=id,display_name,abstract_inverted_index,open_access,best_oa_location")
            candidate = (result.get("results") or [None])[0]
            score = _similar(title, candidate.get("display_name", "")) if candidate else 0
            if not candidate or score < 0.88:
                _attempt(db_path, paper_number, "no_match", candidate.get("display_name") if candidate else None, f"title_similarity={score:.3f}")
                counts["no_match"] += 1
            else:
                location = candidate.get("best_oa_location") or {}
                pdf_url = location.get("pdf_url")
                abstract = _openalex_abstract(candidate.get("abstract_inverted_index"))
                with connect(db_path) as connection:
                    connection.execute("UPDATE papers SET abstract=COALESCE(?,abstract), abstract_source=CASE WHEN ? IS NOT NULL THEN 'openalex' ELSE abstract_source END, scholarly_url=?, pdf_url=COALESCE(?,pdf_url), pdf_source=CASE WHEN ? IS NOT NULL THEN 'openalex' ELSE pdf_source END, pdf_status=CASE WHEN ? IS NOT NULL THEN 'open_access_verified' ELSE pdf_status END, updated_at=CURRENT_TIMESTAMP WHERE paper_number=?", (abstract, abstract, candidate.get("id"), pdf_url, pdf_url, pdf_url, paper_number))
                _attempt(db_path, paper_number, "matched", candidate.get("display_name"), f"title_similarity={score:.3f}")
                refresh_fts_for_paper(db_path, paper_number)
                counts["matched"] += 1
        except Exception as error:  # Retain retryable failures as evidence.
            _attempt(db_path, paper_number, "error", None, str(error)[:500])
            counts["errors"] += 1
        time.sleep(delay)
    return counts


def _attempt(db_path: str, paper_number: str, status: str, matched_title: str | None, detail: str, provider: str = "openalex") -> None:
    with connect(db_path) as connection:
        connection.execute("INSERT INTO enrichment_attempts(paper_number,provider,matched_title,status,detail) VALUES(?,?,?,?,?) ON CONFLICT(paper_number,provider) DO UPDATE SET matched_title=excluded.matched_title,status=excluded.status,detail=excluded.detail,attempted_at=CURRENT_TIMESTAMP", (paper_number, provider, matched_title, status, detail))


def _arxiv_candidates(atom: str) -> list[tuple[str, str]]:
    """Extract canonical paper IDs and titles from arXiv's Atom API."""
    root = ElementTree.fromstring(atom)
    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    candidates = []
    for entry in root.findall("atom:entry", namespace):
        id_element = entry.find("atom:id", namespace)
        title_element = entry.find("atom:title", namespace)
        if id_element is None or title_element is None or not id_element.text or not title_element.text:
            continue
        arxiv_id = re.sub(r"v\d+$", "", id_element.text.rsplit("/abs/", 1)[-1].strip())
        candidates.append((arxiv_id, " ".join(title_element.text.split())))
    return candidates


def _arxiv_candidate(atom: str) -> tuple[str, str] | None:
    """Compatibility helper for callers that only need the first result."""
    candidates = _arxiv_candidates(atom)
    return candidates[0] if candidates else None


_FUZZY_STOPWORDS = {"a", "an", "and", "as", "based", "for", "from", "in", "of", "on", "robot", "robotic", "system", "the", "to", "using", "via", "with"}


def _fuzzy_query(title: str) -> str | None:
    terms = [word.casefold() for word in re.findall(r"[A-Za-z0-9]{4,}", title) if word.casefold() not in _FUZZY_STOPWORDS]
    unique_terms = list(dict.fromkeys(terms))[:4]
    return " AND ".join(f"ti:{term}" for term in unique_terms) if len(unique_terms) >= 2 else None


def _arxiv_fetch(query: str, max_results: int, start: int = 0) -> list[tuple[str, str]]:
    url = f"https://export.arxiv.org/api/query?search_query={quote(query)}&start={start}&max_results={max_results}"
    request = Request(url, headers={"User-Agent": "IROS-paper-catalog/0.1 (metadata client)"})
    with urlopen(request, timeout=30) as response:
        return _arxiv_candidates(response.read().decode("utf-8"))


def _normalise_title(title: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", title.casefold()))


def _exa_query(title: str, first_author: str | None = None) -> str:
    """Describe the exact public record sought, rather than a generic topic."""
    author_hint = f" First author: {first_author}." if first_author else ""
    return (
        "category:research paper Public research record for the exact titled paper "
        f"{title}.{author_hint} Prefer an IEEE Xplore landing page, then an arXiv abstract page, "
        "then an author or lab project page hosted on github.io, then an institutional "
        "repository page. The page must identify the same paper and expose its abstract "
        "or citation. Exclude PDF files and different papers."
    )


def _exa_author_relaxed_query(title: str, first_author: str) -> str:
    """Search by a hard author identity plus the paper's subject, not exact title text."""
    return (
        "category:research paper Public research page for a robotics paper authored by "
        f"{first_author}. The work is about {title}. Prefer IEEE Xplore, arXiv, an author or lab "
        "project page, or an institutional repository. The result must explicitly name the author "
        "and refer to the same research work. Exclude PDFs and different papers."
    )


def _exa_source(url: str) -> tuple[str, str] | None:
    """Return a canonical non-PDF public page and its source type."""
    cleaned = url.strip().split("#", 1)[0]
    if cleaned.lower().endswith(".pdf") or "/pdf/" in cleaned.lower():
        return None
    arxiv = re.search(r"https?://arxiv\.org/(?:abs|html)/([^/?#v]+)(?:v\d+)?", cleaned, re.I)
    if arxiv:
        return (f"https://arxiv.org/abs/{arxiv.group(1)}", "arxiv")
    if "ieeexplore.ieee.org/" in cleaned:
        return (cleaned.rstrip("/"), "ieee")
    if ".github.io/" in cleaned:
        return (cleaned.rstrip("/"), "github_pages")
    if any(host in cleaned for host in (".edu/", ".ac.", "repository.", "dspace.", "handle/", "koasas.")):
        return (cleaned.rstrip("/"), "institutional_repository")
    return None


def _exa_abstract(result: dict) -> str | None:
    """Extract a bounded abstract from Exa's text/highlight response."""
    pieces = result.get("highlights") or []
    if isinstance(result.get("text"), str):
        pieces.append(result["text"])
    text = "\n".join(piece for piece in pieces if isinstance(piece, str))
    match = re.search(r"(?:^|\n)\s*(?:#+\s*)?abstract\s*[:—-]?\s*(.+)", text, re.I | re.S)
    if not match:
        return None
    abstract = re.split(r"\n\s*(?:#+\s*)?(?:keywords?|index terms?|subjects?|introduction|i\.)\b", match.group(1), maxsplit=1, flags=re.I)[0]
    abstract = " ".join(abstract.split())
    return abstract[:6000] or None


def _author_forms(author: str | None) -> set[str]:
    if not author:
        return set()
    direct = _normalise_title(author)
    parts = [part.strip() for part in author.split(",")]
    reverse = _normalise_title(" ".join(reversed(parts))) if len(parts) == 2 else ""
    return {form for form in (direct, reverse) if form}


def _relaxed_title_match(title: str, identity: str) -> bool:
    terms = [word for word in _normalise_title(title).split() if word not in _FUZZY_STOPWORDS and len(word) > 2]
    unique = set(terms)
    if not unique:
        return False
    overlap = len(unique.intersection(identity.split()))
    required = min(len(unique), max(2, (len(unique) + 1) // 2))
    return overlap >= required


def _exa_match(
    title: str, results: list[dict], first_author: str | None = None, relaxed_author: bool = False
) -> tuple[str, str, str | None, str | None] | None:
    """Require an exact title in Exa's result before linking a public page."""
    wanted = _normalise_title(title)
    ranked: list[tuple[int, str, str, str | None, str | None]] = []
    for result in results:
        url = result.get("url")
        result_title = result.get("title") or ""
        normalised_result_title = _normalise_title(result_title)
        page_identity = _normalise_title(" ".join(
            piece for piece in [result_title, *(result.get("highlights") or []), result.get("text") or ""] if isinstance(piece, str)
        ))
        exact_title = (
            normalised_result_title == wanted
            or normalised_result_title.startswith(wanted + " ")
            or wanted in page_identity
        )
        # The recovery pass deliberately relaxes title equality, but only when
        # the page explicitly names the catalog's first author and has a
        # majority of the title's distinctive terms. This remains stricter
        # than an author-only match.
        author_ok = bool(_author_forms(first_author).intersection(
            form for form in _author_forms(first_author) if form in page_identity
        ))
        valid = (author_ok and _relaxed_title_match(title, page_identity)) if relaxed_author else exact_title
        if not isinstance(url, str) or not valid:
            continue
        source = _exa_source(url)
        if not source:
            continue
        public_url, source_type = source
        priority = _PUBLIC_PAGE_SOURCES.index(source_type)
        arxiv_id = re.search(r"arxiv\.org/(?:abs|html)/([^/?#v]+)", public_url)
        ranked.append((priority, public_url, source_type, arxiv_id.group(1) if arxiv_id else None, _exa_abstract(result)))
    if not ranked:
        return None
    _, public_url, source_type, arxiv_id, abstract = min(ranked, key=lambda item: item[0])
    return public_url, source_type, arxiv_id, abstract


def _exa_failure_detail(query: str, results: list[dict]) -> str:
    """Keep the actual search evidence for a later, broader recovery pass."""
    candidates = [
        {"title": item.get("title"), "url": item.get("url")}
        for item in results
        if isinstance(item, dict)
    ]
    return json.dumps({"query": query, "candidates": candidates}, ensure_ascii=False)


def enrich_exa_public(
    db_path: str,
    api_key: str | None = None,
    limit: int = 100,
    delay: float = 0.2,
    abstract_only: bool = False,
    after_paper_number: int | None = None,
    only_untried: bool = False,
    relaxed_author_retry: bool = False,
) -> dict[str, int]:
    """Link unresolved papers to verified public metadata pages via Exa.

    Exactly one Exa request is made per selected unresolved record, serially at
    no more than five requests per second. A result must
    contain the exact normalized title and a supported *non-PDF* public page.
    Failed searches advance the bounded retry counter; only the third failure
    moves a record to the separately queryable manual-review queue.
    """
    key = api_key or os.environ.get("EXA_API_KEY")
    if not key:
        raise ValueError("EXA_API_KEY is required for enrich-exa-public")
    with connect(db_path) as connection:
        if abstract_only:
            rows = connection.execute(
                "SELECT p.paper_number,p.title,"
                "(SELECT a.name FROM paper_authors pa JOIN authors a ON a.id=pa.author_id WHERE pa.paper_number=p.paper_number ORDER BY pa.position LIMIT 1) AS first_author "
                "FROM papers p WHERE public_page_url IS NOT NULL AND (abstract IS NULL OR trim(abstract)='') "
                "ORDER BY CAST(paper_number AS INTEGER) LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        else:
            suffix = ""
            values: list[int] = []
            if only_untried:
                suffix += " AND p.public_link_attempts=0"
            if relaxed_author_retry:
                suffix += " AND p.public_link_status='retry' AND p.public_link_attempts=1"
            if after_paper_number is not None:
                suffix += " AND CAST(p.paper_number AS INTEGER) > ?"
                values.append(after_paper_number)
            values.append(max(1, min(limit, 500)))
            rows = connection.execute(
                "SELECT p.paper_number,p.title,"
                "(SELECT a.name FROM paper_authors pa JOIN authors a ON a.id=pa.author_id WHERE pa.paper_number=p.paper_number ORDER BY pa.position LIMIT 1) AS first_author "
                "FROM papers p WHERE p.public_page_url IS NULL AND p.public_link_attempts <= 2"
                + suffix
                + " ORDER BY p.public_link_attempts, CAST(p.paper_number AS INTEGER) LIMIT ?",
                values,
            ).fetchall()
    counts = {"selected": len(rows), "linked": 0, "with_abstract": 0, "retry": 0, "manual_review": 0, "no_abstract": 0, "errors": 0}
    for position, row in enumerate(rows):
        paper_number, title, first_author = row["paper_number"], row["title"], row["first_author"]
        try:
            query = _exa_author_relaxed_query(title, first_author) if relaxed_author_retry and first_author else _exa_query(title, first_author)
            response = _post_json(
                EXA_SEARCH_URL,
                {"query": query, "numResults": 5, "contents": {"highlights": {"maxCharacters": 5000}, "text": {"maxCharacters": 5000}}},
                {"x-api-key": key, "Content-Type": "application/json", "User-Agent": "IROS-paper-catalog/0.1"},
            )
            candidate = _exa_match(title, response.get("results") or [], first_author, relaxed_author_retry)
            if abstract_only:
                abstract = candidate[3] if candidate else None
                if abstract:
                    with connect(db_path) as connection:
                        connection.execute(
                            "UPDATE papers SET abstract=COALESCE(NULLIF(abstract,''),?), abstract_source=CASE WHEN abstract IS NULL OR abstract='' THEN 'exa' ELSE abstract_source END, updated_at=CURRENT_TIMESTAMP WHERE paper_number=?",
                            (abstract, paper_number),
                        )
                    refresh_fts_for_paper(db_path, paper_number)
                    counts["with_abstract"] += 1
                else:
                    _attempt(db_path, paper_number, "no_abstract", title if candidate else None, "No extractable abstract in this Exa result.", provider="exa_abstract")
                    counts["no_abstract"] += 1
                if position + 1 < len(rows):
                    time.sleep(delay)
                continue
            if candidate:
                public_url, source_type, arxiv_id, abstract = candidate
                if relaxed_author_retry:
                    abstract = None
                with connect(db_path) as connection:
                    connection.execute(
                        "UPDATE papers SET public_page_url=?, public_page_source=?, public_link_status='linked', "
                        "arxiv_id=COALESCE(arxiv_id, ?), scholarly_url=CASE WHEN ?='arxiv' THEN ? ELSE scholarly_url END, "
                        "ieee_url=CASE WHEN ?='ieee' THEN ? ELSE ieee_url END, "
                        "abstract=COALESCE(NULLIF(abstract,''), ?), "
                        "abstract_source=CASE WHEN (abstract IS NULL OR abstract='') AND ? IS NOT NULL THEN 'exa' ELSE abstract_source END, "
                        "updated_at=CURRENT_TIMESTAMP WHERE paper_number=?",
                        (public_url, source_type, arxiv_id, source_type, public_url, source_type, public_url, abstract, abstract, paper_number),
                    )
                    connection.execute(
                        "INSERT INTO enrichment_attempts(paper_number,provider,matched_title,status,detail) VALUES(?,?,?,?,?) "
                        "ON CONFLICT(paper_number,provider) DO UPDATE SET matched_title=excluded.matched_title,status=excluded.status,detail=excluded.detail,attempted_at=CURRENT_TIMESTAMP",
                        (paper_number, "exa_author_relaxed" if relaxed_author_retry else "exa", title, "matched", f"source={source_type}; public_page={public_url}; first_author={first_author}"),
                    )
                refresh_fts_for_paper(db_path, paper_number)
                counts["linked"] += 1
                counts["with_abstract"] += int(abstract is not None)
            else:
                with connect(db_path) as connection:
                    paper = connection.execute("SELECT public_link_attempts FROM papers WHERE paper_number=?", (paper_number,)).fetchone()
                    attempts = paper["public_link_attempts"] + 1
                    status = "manual_review" if attempts > 2 else "retry"
                    connection.execute("UPDATE papers SET public_link_attempts=?,public_link_status=?,updated_at=CURRENT_TIMESTAMP WHERE paper_number=?", (attempts, status, paper_number))
                    connection.execute(
                        "INSERT INTO enrichment_attempts(paper_number,provider,matched_title,status,detail) VALUES(?,?,?,?,?) "
                        "ON CONFLICT(paper_number,provider) DO UPDATE SET status=excluded.status,detail=excluded.detail,attempted_at=CURRENT_TIMESTAMP",
                        (paper_number, "exa_author_relaxed" if relaxed_author_retry else "exa", None, status, _exa_failure_detail(query, response.get("results") or [])),
                    )
                counts[status] += 1
        except Exception as error:
            _attempt(db_path, paper_number, "error", None, str(error)[:500], provider="exa_author_relaxed" if relaxed_author_retry else "exa")
            counts["errors"] += 1
            # An inactive/revoked credential cannot recover by trying more
            # titles. Stop immediately without consuming retry attempts.
            if isinstance(error, HTTPError) and error.code in {401, 403}:
                raise RuntimeError(f"Exa authentication failed ({error.code}); batch stopped before further records") from error
        if position + 1 < len(rows):
            time.sleep(delay)
    return counts


ARXIV_ROBOTICS_CATEGORIES = ("cs.RO", "cs.AI", "cs.LG", "cs.CV", "eess.SY", "eess.IV", "cs.SY", "cs.MA", "math.OC", "physics.app-ph")


def harvest_arxiv_bulk(db_path: str, delay: float = 3.0, max_results: int = 2000, start: int | None = None, categories: tuple[str, ...] | None = None) -> dict[str, int]:
    """Match IROS titles locally after a small set of broad arXiv API pulls.

    This is intentionally a category harvest rather than one network request
    per paper. It makes one paced request per robotics-adjacent category, then
    performs exact normalized-title joins in SQLite/Python locally.
    """
    with connect(db_path) as connection:
        title_to_papers: dict[str, list[tuple[str, str]]] = {}
        for row in connection.execute("SELECT paper_number,title FROM papers WHERE arxiv_id IS NULL"):
            title_to_papers.setdefault(_normalise_title(row["title"]), []).append((row["paper_number"], row["title"]))
    stats = {"categories": 0, "fetched": 0, "matched": 0, "already_linked": 0, "errors": 0}
    selected_categories = categories or ARXIV_ROBOTICS_CATEGORIES
    unknown = set(selected_categories).difference(ARXIV_ROBOTICS_CATEGORIES)
    if unknown:
        raise ValueError(f"Unknown arXiv categories: {', '.join(sorted(unknown))}")
    for category in selected_categories:
        # IROS 2026 papers appeared as preprints mainly in 2025/2026. Sorting
        # newest first keeps the capped page focused on the relevant period.
        with connect(db_path) as connection:
            saved = connection.execute("SELECT next_start FROM arxiv_harvest_state WHERE category=?", (category,)).fetchone()
        page_start = start if start is not None else (saved["next_start"] if saved else 0)
        query = f"cat:{category} AND submittedDate:[202501010000 TO 202612312359]"
        try:
            # Query paging is part of the official API contract. Each later
            # run starts where the prior run ended rather than re-fetching the
            # same top 2,000 records.
            candidates = _arxiv_fetch(query, max_results, page_start)
            stats["categories"] += 1
            stats["fetched"] += len(candidates)
            with connect(db_path) as connection:
                for arxiv_id, arxiv_title in candidates:
                    for paper_number, official_title in title_to_papers.get(_normalise_title(arxiv_title), []):
                        changed = connection.execute(
                            "UPDATE papers SET arxiv_id=?, pdf_url=?, pdf_source='arxiv', pdf_status='open_access_verified', scholarly_url=COALESCE(scholarly_url, ?) WHERE paper_number=? AND arxiv_id IS NULL",
                            (arxiv_id, f"https://arxiv.org/pdf/{arxiv_id}", f"https://arxiv.org/abs/{arxiv_id}", paper_number),
                        ).rowcount
                        if changed:
                            connection.execute("INSERT INTO enrichment_attempts(paper_number,provider,matched_title,status,detail) VALUES(?,?,?,?,?) ON CONFLICT(paper_number,provider) DO UPDATE SET matched_title=excluded.matched_title,status=excluded.status,detail=excluded.detail,attempted_at=CURRENT_TIMESTAMP", (paper_number, "arxiv_bulk", arxiv_title, "matched", f"category={category}; method=normalized_exact"))
                            stats["matched"] += 1
                        else:
                            stats["already_linked"] += 1
                connection.execute("INSERT INTO arxiv_harvest_state(category,next_start) VALUES(?,?) ON CONFLICT(category) DO UPDATE SET next_start=excluded.next_start,updated_at=CURRENT_TIMESTAMP", (category, page_start + max_results))
        except Exception:
            stats["errors"] += 1
        time.sleep(delay)
    return stats


def enrich_arxiv(db_path: str, limit: int | None = None, delay: float = 3.0, retry_no_match: bool = False) -> dict[str, int]:
    """Find title-matched arXiv preprints and store their canonical PDF URLs.

    This queries arXiv's official Atom metadata API, rather than treating a
    search-engine result as a paper. The title threshold protects against a
    similarly named preprint being attached to the wrong IROS submission.
    """
    with connect(db_path) as connection:
        excluded = "'matched'" if retry_no_match else "'matched','no_match'"
        sql = f"SELECT paper_number,title FROM papers WHERE paper_number NOT IN (SELECT paper_number FROM enrichment_attempts WHERE provider='arxiv' AND status IN ({excluded})) ORDER BY paper_number"
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = connection.execute(sql).fetchall()
    counts = {"matched": 0, "no_match": 0, "errors": 0}
    for row in rows:
        paper_number, title = row["paper_number"], row["title"]
        try:
            candidates = _arxiv_fetch(f'ti:"{title}"', 1)
            method = "exact"
            score = max((_similar(title, candidate[1]) for candidate in candidates), default=0)
            # Exact phrase lookup can miss punctuation, subtitle, or title
            # revisions. Retry with four distinctive title terms, then score
            # every returned candidate rather than trusting search rank.
            if score < 0.93 and (fuzzy := _fuzzy_query(title)):
                time.sleep(delay)
                candidates = _arxiv_fetch(fuzzy, 10)
                method = "fuzzy"
            candidate = max(candidates, key=lambda item: _similar(title, item[1]), default=None)
            score = _similar(title, candidate[1]) if candidate else 0
            if not candidate or score < 0.84:
                _arxiv_attempt(db_path, paper_number, "no_match", candidate[1] if candidate else None, f"method={method}; title_similarity={score:.3f}")
                counts["no_match"] += 1
            else:
                arxiv_id = candidate[0]
                with connect(db_path) as connection:
                    connection.execute("UPDATE papers SET arxiv_id=?, pdf_url=?, pdf_source='arxiv', pdf_status='open_access_verified', scholarly_url=COALESCE(scholarly_url,?), updated_at=CURRENT_TIMESTAMP WHERE paper_number=?", (arxiv_id, f"https://arxiv.org/pdf/{arxiv_id}", f"https://arxiv.org/abs/{arxiv_id}", paper_number))
                _arxiv_attempt(db_path, paper_number, "matched", candidate[1], f"method={method}; title_similarity={score:.3f}")
                counts["matched"] += 1
        except Exception as error:
            _arxiv_attempt(db_path, paper_number, "error", None, str(error)[:500])
            counts["errors"] += 1
        time.sleep(delay)
    return counts


def _arxiv_attempt(db_path: str, paper_number: str, status: str, matched_title: str | None, detail: str) -> None:
    with connect(db_path) as connection:
        connection.execute("INSERT INTO enrichment_attempts(paper_number,provider,matched_title,status,detail) VALUES(?,?,?,?,?) ON CONFLICT(paper_number,provider) DO UPDATE SET matched_title=excluded.matched_title,status=excluded.status,detail=excluded.detail,attempted_at=CURRENT_TIMESTAMP", (paper_number, "arxiv", matched_title, status, detail))
