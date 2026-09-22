import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from iros_catalog.api import create_app
from iros_catalog.atlas import build_atlas
from iros_catalog.catalog import import_official_records
from iros_catalog.db import initialize


class MCPTransportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Path(self.directory.name) / "atlas.sqlite"
        initialize(self.db)
        import_official_records(str(self.db), [{
            "pn": "1", "title": "Diffusion Grasping", "authors": "Ada Lovelace",
            "keywords": "Diffusion; Grasping", "session": "Robot Learning",
            "official_record_url": "https://example.test/1",
        }])
        build_atlas(self.db)
        # Deployment overrides are covered separately, without inheriting a
        # developer's production connector environment into local-default tests.
        self.environment = patch.dict("os.environ", {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        self.initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"},
        }}

    @staticmethod
    def message(response):
        if response.headers.get("content-type", "").startswith("text/event-stream"):
            return json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith("data: ")))
        return response.json()

    def test_local_ports_support_complete_browser_session(self):
        with TestClient(create_app(self.db), base_url="http://127.0.0.1:8080") as client:
            for host in ("localhost:8080", "127.0.0.1:8080", "localhost:9123"):
                response = client.post("/mcp/", headers={**self.headers, "Host": host}, json=self.initialize)
                self.assertEqual(response.status_code, 200, response.text)

            headers = {**self.headers, "Origin": "http://localhost:5173"}
            response = client.post("/mcp/", headers=headers, json=self.initialize)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers["access-control-allow-origin"], headers["Origin"])
            self.assertIn("mcp-session-id", response.headers["access-control-expose-headers"].lower())
            headers.update({"Mcp-Session-Id": response.headers["mcp-session-id"], "MCP-Protocol-Version": "2025-06-18"})
            response = client.post("/mcp/", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
            self.assertEqual(response.status_code, 202)
            response = client.post("/mcp/", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            self.assertEqual(response.status_code, 200)
            self.assertIn("search_papers", {tool["name"] for tool in self.message(response)["result"]["tools"]})
            response = client.post("/mcp/", headers=headers, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "search_papers", "arguments": {"query": "diffusion"}}})
            self.assertEqual(response.status_code, 200)
            result = self.message(response)["result"]
            self.assertFalse(result["isError"])
            result_data = json.loads(result["content"][0]["text"])
            self.assertEqual(result_data["papers"][0]["paper_number"], "1")
            response = client.post("/mcp/", headers=headers, json={"jsonrpc": "2.0", "id": 4, "method": "resources/read", "params": {"uri": "iros://methodology/atlas-score"}})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(json.loads(self.message(response)["result"]["contents"][0]["text"])["name"], "Atlas Score")
            self.assertEqual(client.delete("/mcp/", headers=headers).status_code, 200)

    def test_preflight_allows_mcp_methods_without_enabling_rest_writes(self):
        with TestClient(create_app(self.db), base_url="http://localhost:8080") as client:
            for method in ("GET", "POST", "DELETE"):
                response = client.options("/mcp/", headers={
                    "Origin": "http://localhost:5173", "Access-Control-Request-Method": method,
                    "Access-Control-Request-Headers": "content-type,mcp-session-id,mcp-protocol-version",
                })
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.headers["access-control-allow-origin"], "http://localhost:5173")
            response = client.options("/api/v1/papers", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
            self.assertEqual(response.status_code, 400)
            response = client.get("/api/v1/papers", headers={"Origin": "https://reader.example"})
            self.assertEqual(response.headers["access-control-allow-origin"], "*")

    def test_untrusted_host_and_origin_are_rejected(self):
        with TestClient(create_app(self.db), base_url="http://localhost:8080") as client:
            response = client.post("/mcp/", headers={**self.headers, "Host": "attacker.example"}, json=self.initialize)
            self.assertEqual(response.status_code, 421)
            response = client.post("/mcp/", headers={**self.headers, "Origin": "https://attacker.example"}, json=self.initialize)
            self.assertEqual(response.status_code, 403)
            self.assertNotIn("access-control-allow-origin", response.headers)
            response = client.options("/mcp/", headers={"Origin": "https://attacker.example", "Access-Control-Request-Method": "POST"})
            self.assertEqual(response.status_code, 400)

    def test_custom_deployment_allowlists_replace_local_defaults(self):
        with patch.dict("os.environ", {"IROS_ATLAS_ALLOWED_HOSTS": "atlas.example", "IROS_ATLAS_ALLOWED_ORIGINS": "https://reader.example"}):
            with TestClient(create_app(self.db), base_url="https://atlas.example") as client:
                response = client.post("/mcp/", headers={**self.headers, "Origin": "https://reader.example"}, json=self.initialize)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.headers["access-control-allow-origin"], "https://reader.example")
                response = client.post("/mcp/", headers={**self.headers, "Host": "localhost:8080"}, json=self.initialize)
                self.assertEqual(response.status_code, 421)
                response = client.post("/mcp/", headers={**self.headers, "Origin": "http://localhost:5173"}, json=self.initialize)
                self.assertEqual(response.status_code, 403)

    def test_public_url_configures_a_reliable_mcp_endpoint(self):
        public_origin = "https://mcp.irostatlas.example"
        with patch.dict("os.environ", {"IROS_ATLAS_PUBLIC_URL": public_origin}, clear=True):
            with TestClient(create_app(self.db), base_url=public_origin) as client:
                for request_id in range(1, 11):
                    initialize = {**self.initialize, "id": request_id}
                    response = client.post("/mcp/", headers=self.headers, json=initialize)
                    self.assertEqual(response.status_code, 200, response.text)
                    session_id = response.headers["mcp-session-id"]
                    session_headers = {
                        **self.headers,
                        "Mcp-Session-Id": session_id,
                        "MCP-Protocol-Version": "2025-06-18",
                    }
                    response = client.post("/mcp/", headers=session_headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
                    self.assertEqual(response.status_code, 202, response.text)
                    response = client.post("/mcp/", headers=session_headers, json={"jsonrpc": "2.0", "id": request_id, "method": "tools/list"})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertIn("search_papers", {tool["name"] for tool in self.message(response)["result"]["tools"]})
                health = client.get("/api/v1/health").json()
                self.assertEqual(health["mcp"], {
                    "endpoint": f"{public_origin}/mcp/",
                    "transport": "Streamable HTTP",
                    "authentication": "none",
                })
