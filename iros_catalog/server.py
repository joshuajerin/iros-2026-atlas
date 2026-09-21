from __future__ import annotations

import uvicorn

from .api import create_app


def serve(db_path: str, port: int) -> None:
    """Run the production FastAPI and MCP service."""
    uvicorn.run(create_app(db_path), host="127.0.0.1", port=port)
