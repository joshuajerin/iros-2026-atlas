"""MCP tools must honor their requested subject and bounded result count."""

import asyncio
import json

import pytest

from iros_catalog.atlas import build_atlas, keyword_network, paper_detail, rankings
from iros_catalog.catalog import import_official_records
from iros_catalog.db import initialize
from iros_catalog.mcp_server import create_mcp


@pytest.fixture(scope="module")
def catalog(tmp_path_factory):
    db = tmp_path_factory.mktemp("mcp-queries") / "atlas.sqlite"
    initialize(db)
    records = []
    for number in range(1, 23):
        records.append({
            "pn": str(number), "title": f"Manipulation paper {number}",
            "authors": "; ".join([*(f"Researcher {index:04}" for index in range(1001)), "Ziel Straße"]) if number == 1 else "Frequent Researcher",
            "affiliations": "; ".join([*(f"Institute {index:04}" for index in range(501)), "ZZ Straße Institute"]) if number == 1 else "Frequent Institute",
            "keywords": "Z Rare Keyword; Unique Neighbor; Shared" if number == 22 else "; ".join([*(f"Common {index:02}" for index in range(20)), "Shared"]),
            "session": "Manipulation", "official_record_url": f"https://example.test/{number}",
        })
    import_official_records(str(db), records)
    build_atlas(db)
    return db


def call(catalog, name, arguments):
    result = asyncio.run(create_mcp(catalog).call_tool(name, arguments))
    return result if isinstance(result, dict) else json.loads(result[0].text)


def test_keyword_exploration_keeps_rare_seed_and_actual_neighbors(catalog):
    assert "z-rare-keyword" not in {node["id"] for node in keyword_network(catalog, 12)["nodes"]}
    result = call(catalog, "explore_keyword", {"keyword": "Z Rare Keyword", "limit": 12})
    assert [paper["paper_number"] for paper in result["papers"]["papers"]] == ["22"]
    assert {node["id"] for node in result["network"]["nodes"]} == {"z-rare-keyword", "unique-neighbor", "shared"}
    assert {edge["target"] for edge in result["network"]["edges"]} == {"unique-neighbor", "shared"}
    assert all(edge["source"] == "z-rare-keyword" and edge["count"] == 1 for edge in result["network"]["edges"])


def test_missing_keyword_does_not_return_unrelated_graph(catalog):
    result = call(catalog, "explore_keyword", {"keyword": "missing"})
    assert result["papers"]["count"] == 0
    assert result["network"] == {"nodes": [], "edges": []}


@pytest.mark.parametrize(("kind", "tool", "name", "needle", "old_cutoff"), [
    ("researchers", "get_researcher", "Ziel Straße", " ZIEL STRASSE ", 1000),
    ("institutions", "get_institution", "ZZ Straße Institute", " zz STRASSE ", 500),
])
def test_name_search_happens_before_rank_limit(catalog, kind, tool, name, needle, old_cutoff):
    assert name not in {item["name"] for item in rankings(catalog, kind, old_cutoff)["items"]}
    result = call(catalog, tool, {"name": needle, "limit": 1})
    assert [item["name"] for item in result["items"]] == [name]
    assert result["items"][0]["paper_count"] == 1
    assert len(call(catalog, tool, {"name": "", "limit": 1000})["items"]) == 50
    assert call(catalog, tool, {"name": "%"})["items"] == []


@pytest.mark.parametrize(("limit", "expected"), [(0, 1), (6, 6), (12, 12), (20, 20), (1000, 20)])
def test_related_tool_honors_requested_bound(catalog, limit, expected):
    result = call(catalog, "discover_related_papers", {"paper_number": "1", "limit": limit})
    assert result["paper_number"] == "1"
    assert len(result["related_papers"]) == expected
    assert all(paper["paper_number"] != "1" for paper in result["related_papers"])
    assert len(paper_detail(catalog, "1")["related_papers"]) == 6


def test_related_tool_preserves_missing_paper_result(catalog):
    assert call(catalog, "discover_related_papers", {"paper_number": "missing", "limit": 20}) == {"error": "not_found", "paper_number": "missing"}
