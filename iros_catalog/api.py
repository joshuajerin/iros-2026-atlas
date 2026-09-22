"""FastAPI application for the IROS 2026 Atlas."""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .atlas import institution_profile, keyword_network, methodology, overview, paper_detail, rankings, researcher_profile, search_papers, topic_detail
from .mcp_server import allowed_mcp_origins, create_mcp, public_mcp_endpoint


class ScopedCORSMiddleware:
    """Allow browser MCP sessions only from configured connector origins."""

    def __init__(self, app):
        headers = ["Accept", "Content-Type", "MCP-Protocol-Version", "Mcp-Session-Id"]
        self.mcp = CORSMiddleware(
            app, allow_origins=allowed_mcp_origins(), allow_methods=["GET", "POST", "DELETE"],
            allow_headers=headers, expose_headers=["Mcp-Session-Id"],
        )
        self.rest = CORSMiddleware(app, allow_origins=["*"], allow_methods=["GET"], allow_headers=headers)

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        middleware = self.mcp if path == "/mcp" or path.startswith("/mcp/") else self.rest
        await middleware(scope, receive, send)


class RateLimitMiddleware:
    """Small in-process safety rail for the anonymous, read-only service."""

    def __init__(self, app, limit: int = 120, window: int = 60):
        self.app, self.limit, self.window = app, limit, window
        self.hits: defaultdict[str, deque[float]] = defaultdict(deque)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith(("/api", "/mcp")):
            return await self.app(scope, receive, send)
        client = scope.get("client") or ("unknown", 0)
        key = f"{client[0]}:{scope['path'].split('/', 3)[1]}"
        now, bucket = time.monotonic(), self.hits[key]
        while bucket and bucket[0] <= now - self.window:
            bucket.popleft()
        if len(bucket) >= self.limit:
            response = JSONResponse({"detail": "rate limit exceeded"}, status_code=429, headers={"Retry-After": str(self.window)})
            return await response(scope, receive, send)
        bucket.append(now)
        return await self.app(scope, receive, send)


def create_app(db_path: str | Path | None = None) -> FastAPI:
    project_root = Path(__file__).resolve().parents[1]
    database = Path(db_path or os.environ.get("IROS_ATLAS_DB", project_root / "data" / "iros.sqlite"))
    mcp = create_mcp(database)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="IROS 2026 Atlas API", version="0.2.0", description="Read-only research map and agent connector for IROS 2026.", lifespan=lifespan)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(ScopedCORSMiddleware)

    @app.get("/api/v1/health")
    def health() -> dict:
        try:
            data = overview(database)
            return {
                "ok": True,
                "papers": data["stats"]["papers"],
                "dataset_built_at": data["built_at"],
                "mcp": {
                    "endpoint": public_mcp_endpoint(),
                    "transport": "Streamable HTTP",
                    "authentication": "none",
                },
            }
        except RuntimeError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.get("/api/v1/overview")
    def get_overview() -> dict:
        return overview(database)

    @app.get("/api/v1/papers")
    def get_papers(q: str | None = Query(default=None, max_length=200), topic: str | None = Query(default=None, max_length=100), keyword: str | None = Query(default=None, max_length=100), institution: str | None = Query(default=None, max_length=200), sort: str = Query(default="atlas_score", pattern="^(atlas_score|program_order)$"), limit: int = Query(default=30, ge=1, le=100), offset: int = Query(default=0, ge=0, le=10_000)) -> dict:
        return search_papers(database, q, topic, keyword, institution, sort, limit, offset)

    @app.get("/api/v1/papers/{paper_number}")
    def get_paper(paper_number: str) -> dict:
        item = paper_detail(database, paper_number)
        if item is None:
            raise HTTPException(status_code=404, detail="paper not found")
        return item

    @app.get("/api/v1/rankings/{kind}")
    def get_rankings(kind: str, limit: int = Query(default=30, ge=1, le=100), offset: int = Query(default=0, ge=0, le=10_000), topic: str | None = Query(default=None, max_length=100)) -> dict:
        try:
            return rankings(database, kind, limit, topic, offset=offset)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/api/v1/keywords/network")
    def get_keyword_network(limit: int = Query(default=80, ge=12, le=180)) -> dict:
        return keyword_network(database, limit)

    @app.get("/api/v1/topics/{topic}")
    def get_topic(topic: str) -> dict:
        item = topic_detail(database, topic)
        if item is None:
            raise HTTPException(status_code=404, detail="topic not found")
        return item

    @app.get("/api/v1/institutions/{institution}")
    def get_institution(institution: str) -> dict:
        item = institution_profile(database, institution)
        if item is None:
            raise HTTPException(status_code=404, detail="institution not found")
        return item

    @app.get("/api/v1/researchers/{researcher_id}")
    def get_researcher(researcher_id: str) -> dict:
        item = researcher_profile(database, researcher_id)
        if item is None:
            raise HTTPException(status_code=404, detail="researcher not found")
        return item

    @app.get("/api/v1/methodology/atlas-score")
    def get_methodology() -> dict:
        return methodology(database)

    app.mount("/mcp", mcp.streamable_http_app())

    dist = project_root / "web" / "dist"
    if dist.exists():
        if (dist / "assets").exists():
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str, request: Request):
            if path.startswith(("api/", "mcp")):
                raise HTTPException(status_code=404, detail="not found")
            return FileResponse(dist / "index.html")
    return app


app = create_app()
