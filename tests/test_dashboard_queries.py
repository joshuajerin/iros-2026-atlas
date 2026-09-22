"""Regressions for literal search and topic-scoped dashboard entity totals."""

import math

import pytest

from iros_catalog.atlas import build_atlas, rankings, search_papers, topic_detail
from iros_catalog.catalog import import_official_records
from iros_catalog.db import connect, initialize


@pytest.fixture
def catalog(tmp_path):
    db = tmp_path / "atlas.sqlite"
    initialize(db)
    import_official_records(str(db), [
        {"pn": "1", "title": "Multi-agent robot learning", "authors": "Ada; Grace", "keywords": "Learning; Shared Keyword", "affiliations": "Example University", "official_record_url": "https://example.test/1"},
        {"pn": "2", "title": "Deep-learning robot control", "authors": "Ada; Lin", "keywords": "Control; Shared Keyword", "affiliations": "Example University; Control Institute", "official_record_url": "https://example.test/2"},
    ])
    with connect(db) as connection:
        connection.execute("DELETE FROM paper_topics")
        connection.executemany("INSERT INTO paper_topics(paper_number,topic) VALUES (?,?)", [
            ("1", "learning_and_reinforcement_learning"),
            ("1", "manipulation_and_grasping"),
            ("2", "control_and_optimization"),
        ])
    build_atlas(db)
    # Distinct scores catch both duplicate contributions and weighted AVG bugs.
    with connect(db) as connection:
        connection.execute("UPDATE atlas_scores SET score=60 WHERE paper_number='1'")
        connection.execute("UPDATE atlas_scores SET score=20 WHERE paper_number='2'")
    return db


@pytest.mark.parametrize(("query", "numbers"), [
    ("multi-agent", {"1"}),
    ("deep-learning", {"2"}),
    ('"multi-agent" OR', set()),
    ("AND", set()),
    ("-", {"1", "2"}),
    ("multi age", {"1"}),
])
def test_search_treats_words_as_literal_prefixes(catalog, query, numbers):
    result = search_papers(catalog, query=query)
    assert {paper["paper_number"] for paper in result["papers"]} == numbers


def test_researcher_totals_count_each_paper_once(catalog):
    people = {item["name"]: item for item in rankings(catalog, "researchers")["items"]}
    assert (people["Ada"]["paper_count"], people["Ada"]["score"], people["Ada"]["topic_breadth"]) == (2, 40, 3)
    assert (people["Grace"]["paper_count"], people["Grace"]["score"]) == (1, 30)
    assert (people["Lin"]["paper_count"], people["Lin"]["score"]) == (1, 10)


def test_researcher_topic_filter_scopes_counts_and_scores(catalog):
    result = rankings(catalog, "researchers", topic="learning_and_reinforcement_learning")
    assert {item["name"] for item in result["items"]} == {"Ada", "Grace"}
    for item in result["items"]:
        assert (item["paper_count"], item["score"], item["topic_breadth"]) == (1, 30, 2)
    assert rankings(catalog, "researchers", topic="missing")["items"] == []


def test_institution_average_counts_each_paper_once(catalog):
    items = {item["name"]: item for item in rankings(catalog, "institutions")["items"]}
    university = items["Example University"]
    assert university["paper_count"] == 2
    assert university["score"] == round(40 * math.sqrt(2), 2)
    assert university["topic_breadth"] == 3


def test_institution_topic_filter_scopes_all_returned_metrics(catalog):
    result = rankings(catalog, "institutions", topic="learning_and_reinforcement_learning")
    assert len(result["items"]) == 1
    university = result["items"][0]
    assert university["name"] == "Example University"
    assert (university["paper_count"], university["score"], university["topic_breadth"]) == (1, 60, 2)
    assert set(university["top_topics"]) == {"learning_and_reinforcement_learning", "manipulation_and_grasping"}
    assert rankings(catalog, "institutions", topic="missing")["items"] == []


@pytest.mark.parametrize("kind", ["researchers", "institutions"])
def test_entity_rankings_paginate_complete_filtered_index(catalog, kind):
    first = rankings(catalog, kind, limit=1, offset=0)
    second = rankings(catalog, kind, limit=1, offset=1)
    assert first["count"] >= 2
    assert (first["limit"], first["offset"]) == (1, 0)
    assert (second["limit"], second["offset"]) == (1, 1)
    assert first["items"][0]["id"] != second["items"][0]["id"]
    all_items = rankings(catalog, kind, limit=100, offset=0)
    assert len(all_items["items"]) == all_items["count"]


def test_entity_ranking_pagination_applies_topic_before_limit_and_offset(catalog):
    first = rankings(catalog, "researchers", limit=1, topic="learning_and_reinforcement_learning", offset=0)
    second = rankings(catalog, "researchers", limit=1, topic="learning_and_reinforcement_learning", offset=1)
    assert first["count"] == 2
    assert {first["items"][0]["name"], second["items"][0]["name"]} == {"Ada", "Grace"}
    assert all(item["paper_count"] == 1 for item in [*first["items"], *second["items"]])


def test_topic_keywords_count_only_papers_within_selected_area(catalog):
    topic = topic_detail(catalog, "learning_and_reinforcement_learning")
    assert topic["paper_count"] == 1
    words = {item["slug"]: item for item in topic["keywords"]}
    assert set(words) == {"learning", "shared-keyword"}
    assert words["shared-keyword"]["paper_count"] == 1
    # The same keyword is present outside this topic, but not in its subtotal.
    assert search_papers(catalog, keyword="shared-keyword")["count"] == 2
    for word in words.values():
        assert word["paper_count"] == search_papers(catalog, topic=topic["slug"], keyword=word["slug"])["count"]


def test_missing_topic_and_topic_without_keywords_are_safe(catalog):
    assert topic_detail(catalog, "missing") is None
    with connect(catalog) as connection:
        connection.execute("DELETE FROM atlas_paper_keywords WHERE paper_number='1'")
    topic = topic_detail(catalog, "learning_and_reinforcement_learning")
    assert topic["paper_count"] == 1
    assert topic["keywords"] == []
