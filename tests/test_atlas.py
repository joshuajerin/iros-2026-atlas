import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from iros_catalog.api import create_app
from iros_catalog.atlas import build_atlas, keyword_network, methodology, rankings, search_papers
from iros_catalog.catalog import import_official_records
from iros_catalog.db import initialize


class AtlasTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = Path(self.directory.name) / "atlas.sqlite"
        initialize(self.db)
        import_official_records(str(self.db), [
            {"pn": "1", "title": "Diffusion Grasping for Warehouse Robots", "authors": "Ada Lovelace; Grace Hopper", "keywords": "Diffusion; Grasping", "session": "Robot Learning", "type": "Talk", "affiliations": "Example University", "official_record_url": "https://example.test/1", "abstract": "A manipulation paper."},
            {"pn": "2", "title": "Diffusion Maps for Mobile Robots", "authors": "Ada Lovelace; Lin Chen", "keywords": "Diffusion; Mapping", "session": "Navigation", "type": "Talk", "affiliations": "Example Institute", "official_record_url": "https://example.test/2", "abstract": "A navigation paper."},
        ])
        build_atlas(self.db)

    def tearDown(self):
        self.directory.cleanup()

    def test_keywords_are_normalized_and_search_filters_compose(self):
        results = search_papers(self.db, query="diffusion", keyword="Diffusion", limit=10)
        self.assertEqual(results["count"], 2)
        self.assertEqual({paper["paper_number"] for paper in results["papers"]}, {"1", "2"})

    def test_missing_citations_get_neutral_prior_and_confident_score(self):
        item = search_papers(self.db, limit=1)["papers"][0]
        self.assertEqual(item["score"]["components"]["citation_velocity"], 50.0)
        self.assertFalse(item["score"]["eligible"])
        self.assertIn("Missing external metrics", " ".join(methodology(self.db)["rules"]))

    def test_api_and_mcp_initialize(self):
        with TestClient(create_app(self.db), base_url="http://localhost") as client:
            self.assertEqual(client.get("/api/v1/overview").status_code, 200)
            self.assertEqual(client.get("/api/v1/keywords/network?limit=12").status_code, 200)
            response = client.post("/mcp/", headers={"Host": "localhost", "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}})
            self.assertEqual(response.status_code, 200)
            self.assertIn("IROS 2026 Atlas", response.text)

    def test_entity_ranking_api_validates_and_returns_pagination_metadata(self):
        with TestClient(create_app(self.db), base_url="http://localhost") as client:
            response = client.get("/api/v1/rankings/researchers?limit=1&offset=1")
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual((payload["limit"], payload["offset"]), (1, 1))
            self.assertGreaterEqual(payload["count"], 3)
            self.assertEqual(len(payload["items"]), 1)
            self.assertEqual(client.get("/api/v1/rankings/researchers?offset=-1").status_code, 422)

    def test_keyword_nodes_use_the_keyword_semantic_topic_not_a_papers_first_topic(self):
        # This paper belongs to both topics.  The old derived-data build chose
        # ``control_and_optimization`` only because it sorts first, which made
        # clicking Reinforcement Learning open the wrong dashboard branch.
        import_official_records(str(self.db), [{
            "pn": "3", "title": "Reinforcement Learning Control for Robots",
            "authors": "Grace Hopper", "keywords": "Reinforcement Learning; Control",
            "session": "Robot Learning", "type": "Talk", "affiliations": "Example University",
            "official_record_url": "https://example.test/3",
        }])
        build_atlas(self.db)

        nodes = {node["id"]: node for node in keyword_network(self.db, limit=20)["nodes"]}
        self.assertEqual(nodes["reinforcement-learning"]["topic"], "learning_and_reinforcement_learning")
