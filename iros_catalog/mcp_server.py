"""Read-only MCP surface sharing the exact same Atlas service layer as REST."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .atlas import keyword_network, methodology, paper_detail, rankings, search_papers as search_catalog, topic_detail


LOCAL_MCP_HOSTS = ["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*"]
LOCAL_MCP_ORIGINS = [
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:8080", "http://127.0.0.1:8080",
]


def _configured_public_origins() -> list[str]:
    """Return validated public origins from the single deployment setting.

    A named Cloudflare Tunnel preserves the external Host header.  Keeping the
    published origin in one setting prevents the application and tunnel from
    drifting into the 421 configuration that a temporary tunnel can cause.
    """
    values = [value.strip() for value in os.environ.get("IROS_ATLAS_PUBLIC_URL", "").split(",") if value.strip()]
    origins: list[str] = []
    for value in values:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError(
                "IROS_ATLAS_PUBLIC_URL must be a comma-separated http(s) origin, "
                "for example https://mcp.example.com"
            )
        if parsed.path not in {"", "/", "/mcp", "/mcp/"}:
            raise ValueError("IROS_ATLAS_PUBLIC_URL cannot contain an application path")
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in origins:
            origins.append(origin)
    return origins


def public_mcp_endpoint() -> str | None:
    """The exact, canonical public Streamable HTTP endpoint when configured."""
    origins = _configured_public_origins()
    return f"{origins[0]}/mcp/" if origins else None


def allowed_mcp_hosts() -> list[str]:
    configured = os.environ.get("IROS_ATLAS_ALLOWED_HOSTS")
    if configured is not None:
        return [host.strip() for host in configured.split(",") if host.strip()]
    public_origins = _configured_public_origins()
    return [urlsplit(origin).netloc for origin in public_origins] or LOCAL_MCP_HOSTS


def allowed_mcp_origins() -> list[str]:
    configured = os.environ.get("IROS_ATLAS_ALLOWED_ORIGINS")
    if configured is not None:
        return [origin.strip() for origin in configured.split(",") if origin.strip()]
    return _configured_public_origins() or LOCAL_MCP_ORIGINS


def create_mcp(db_path: str | Path) -> FastMCP:
    mcp = FastMCP(
        "IROS 2026 Atlas",
        instructions="Read-only IROS 2026 papers, rankings, topics, institutions, and researchers. Cite returned evidence URLs.",
        streamable_http_path="/",
        max_request_body_size=128_000,
        transport_security=TransportSecuritySettings(allowed_hosts=allowed_mcp_hosts(), allowed_origins=allowed_mcp_origins()),
    )
    path = str(db_path)

    @mcp.tool()
    def search_papers(query: str = "", topic: str = "", keyword: str = "", limit: int = 10) -> dict:
        """Search the official IROS 2026 catalog with safe, bounded filters."""
        return search_catalog(path, query or None, topic or None, keyword or None, limit=min(max(limit, 1), 50))

    @mcp.tool()
    def get_paper(paper_number: str) -> dict:
        """Get one paper with its score, evidence, verified public links, and related work."""
        result = paper_detail(path, paper_number)
        return result or {"error": "not_found", "paper_number": paper_number}

    @mcp.tool()
    def rank_papers(topic: str = "", limit: int = 10) -> dict:
        """Return eligible papers ordered by the documented Atlas Score."""
        return rankings(path, "papers", min(max(limit, 1), 50), topic or None)

    @mcp.tool()
    def explore_keyword(keyword: str, limit: int = 24) -> dict:
        """Find papers and neighboring conference keywords for a keyword."""
        return {"papers": search_catalog(path, keyword=keyword, limit=min(max(limit, 1), 50)), "network": keyword_network(path, min(max(limit, 12), 100), keyword=keyword)}

    @mcp.tool()
    def compare_topics(topic_a: str, topic_b: str) -> dict:
        """Compare two IROS taxonomy topics without unsupported causal claims."""
        return {"a": topic_detail(path, topic_a), "b": topic_detail(path, topic_b)}

    @mcp.tool()
    def get_institution(name: str, limit: int = 20) -> dict:
        """Search IROS 2026 institution presence rankings."""
        return {"items": rankings(path, "institutions", min(max(limit, 1), 50), name=name)["items"]}

    @mcp.tool()
    def get_researcher(name: str, limit: int = 20) -> dict:
        """Search source-name researcher profiles; ambiguous identities remain unmerged."""
        return {"items": rankings(path, "researchers", min(max(limit, 1), 50), name=name)["items"]}

    @mcp.tool()
    def discover_related_papers(paper_number: str, limit: int = 6) -> dict:
        """Return topic-adjacent IROS papers for one seed paper."""
        paper = paper_detail(path, paper_number, related_limit=min(max(limit, 1), 20))
        if not paper:
            return {"error": "not_found", "paper_number": paper_number}
        return {"paper_number": paper_number, "related_papers": paper["related_papers"]}

    @mcp.tool()
    def build_reading_list(topic: str, limit: int = 8) -> dict:
        """Create a transparent, score-ordered IROS reading list for a topic."""
        return {"topic": topic, "papers": rankings(path, "papers", min(max(limit, 1), 30), topic)["items"], "methodology": methodology(path)}

    @mcp.resource("iros://methodology/atlas-score")
    def score_methodology() -> str:
        return json.dumps(methodology(path), indent=2)

    @mcp.resource("iros://rankings/overall")
    def overall_rankings() -> str:
        return json.dumps(rankings(path, "papers", 25), indent=2)

    @mcp.resource("iros://paper/{paper_number}")
    def paper_resource(paper_number: str) -> str:
        return json.dumps(paper_detail(path, paper_number) or {"error": "not_found"}, indent=2)

    @mcp.resource("iros://topic/{topic}")
    def topic_resource(topic: str) -> str:
        return json.dumps(topic_detail(path, topic) or {"error": "not_found"}, indent=2)

    return mcp
