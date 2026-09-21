"""Import the public, embedded data on IROS' official paper-index page."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from urllib.request import Request, urlopen

OFFICIAL_INDEX_URL = "https://2026.ieee-iros.org/program/paper-index/"
# The official index defers paper abstracts to this first-party dataset. The
# browser page itself documents that it is shared by the program views and the
# paper-index abstract disclosure.
OFFICIAL_SCHEDULE_DATA_URL = "https://2026.ieee-iros.org/program/assets/schedule_data.json"
# The official IROS index's Files menu calls these Infovaya records, although
# the canonical first-party URL is served from rasevents.org.
INFOVAYA_RECORD_URL = "https://rasevents.org/presentation?id={id}"


class ScriptCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_script = False
        self._chunks: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self._in_script = True
            self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self._chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_script:
            self.scripts.append("".join(self._chunks))
            self._in_script = False


def fetch_official_html(url: str = OFFICIAL_INDEX_URL) -> str:
    request = Request(url, headers={"User-Agent": "IROS-paper-catalog/0.1 (research metadata importer)"})
    with urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8")


def fetch_official_schedule_data(url: str = OFFICIAL_SCHEDULE_DATA_URL) -> dict:
    """Fetch the official IROS program dataset that contains paper abstracts."""
    request = Request(url, headers={"User-Agent": "IROS-paper-catalog/0.1 (research metadata importer)"})
    with urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("views"), dict):
        raise ValueError("Official IROS schedule dataset has an unexpected shape")
    return payload


def extract_official_paper_details(dataset: dict) -> dict[str, dict[str, str]]:
    """Return the official abstract and keywords for each paper number.

    The schedule data groups papers by program view and theme. Paper numbers
    are globally unique, so they are the stable key for joining it to the
    paper-index import.
    """
    details: dict[str, dict[str, str]] = {}
    for view in dataset.get("views", {}).values():
        if not isinstance(view, dict):
            continue
        for papers in view.get("themes", {}).values():
            if not isinstance(papers, list):
                continue
            for paper in papers:
                if not isinstance(paper, dict) or not paper.get("pn"):
                    continue
                paper_number = str(paper["pn"])
                detail = {
                    "abstract": str(paper.get("abstract") or "").strip(),
                    "keywords": str(paper.get("keywords") or "").strip(),
                }
                existing = details.get(paper_number)
                if existing and existing != detail:
                    raise ValueError(f"Conflicting official details for paper {paper_number}")
                details[paper_number] = detail
    return details


def extract_official_records(html: str) -> list[dict]:
    parser = ScriptCollector()
    parser.feed(html)
    papers: list[dict] | None = None
    ids: dict[str, str] = {}
    affiliations: dict[str, str] = {}
    decoder = json.JSONDecoder()
    for script in parser.scripts:
        trimmed = script.lstrip()
        if papers is None and trimmed.startswith("["):
            try:
                candidate, _ = decoder.raw_decode(trimmed)
                if isinstance(candidate, list) and candidate and isinstance(candidate[0], dict) and {"pn", "title"} <= candidate[0].keys():
                    papers = candidate
            except json.JSONDecodeError:
                pass
        match = re.search(r"var\s+PAPER_IDS\s*=\s*", script)
        if match:
            try:
                candidate, _ = decoder.raw_decode(script[match.end():].lstrip())
                if isinstance(candidate, dict):
                    ids = {str(key): str(value) for key, value in candidate.items()}
            except json.JSONDecodeError:
                pass
    if papers is None:
        raise ValueError("Could not find the official paper array in the IROS index page")
    paper_numbers = {str(paper["pn"]) for paper in papers}
    # A second embedded JSON object is the official positional affiliation map:
    # paper number -> '; '-joined institution names. Locate it structurally so
    # a visual template change does not couple the importer to script indexes.
    for script in parser.scripts:
        trimmed = script.lstrip()
        if not trimmed.startswith("{"):
            continue
        try:
            candidate, _ = decoder.raw_decode(trimmed)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and candidate and all(isinstance(value, str) for value in candidate.values()):
            overlap = set(map(str, candidate)).intersection(paper_numbers)
            if len(overlap) > len(paper_numbers) * 0.9:
                affiliations = {str(key): value for key, value in candidate.items()}
                break
    for paper in papers:
        paper["rasevents_id"] = ids.get(str(paper["pn"]))
        paper["infovaya_record"] = INFOVAYA_RECORD_URL.format(id=paper["rasevents_id"]) if paper["rasevents_id"] else None
        paper["official_record_url"] = paper["infovaya_record"]
        paper["affiliations"] = affiliations.get(str(paper["pn"]), "")
    return papers
