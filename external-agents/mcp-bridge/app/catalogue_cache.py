"""In-process catalogue cache — fetches tool metadata from all MCP servers at startup.

Mirrors backend/agent-orchestrator/app/tools/catalogue.py — ported for the bridge.

Collision rule: if both servers expose a tool with the same name (e.g. query_asset),
the analysis server entry wins (first-server-wins; both implementations are identical).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.bridge import fetch_catalogue
from app.config import settings

logger = logging.getLogger(__name__)

# Ordered list — analysis server is first so it wins on name collisions
_MCP_SERVERS = [
    settings.mcp_analysis_url,
    settings.mcp_processing_url,
]

# O(1) lookup by tool name → tool descriptor (includes _server_url)
_tool_index: dict[str, dict[str, Any]] = {}


async def warm_catalogue() -> None:
    """Fetch tool catalogues from all MCP servers and populate the in-process index."""
    global _tool_index
    results = await asyncio.gather(
        *[fetch_catalogue(url) for url in _MCP_SERVERS],
        return_exceptions=True,
    )
    index: dict[str, dict] = {}
    for result in results:
        if isinstance(result, Exception):
            logger.warning("catalogue fetch error: %s", result)
            continue
        for tool in result:
            name = tool.get("name")
            if name and name not in index:
                # First-server-wins: analysis server entries are processed first
                index[name] = tool
    _tool_index = index
    logger.info("catalogue warmed: %d tools loaded", len(_tool_index))


def get_tool_index() -> dict[str, dict[str, Any]]:
    return _tool_index


def get_tools_list() -> list[dict[str, Any]]:
    return list(_tool_index.values())


async def refresh_loop() -> None:
    """Background task: refresh the catalogue every catalogue_refresh_interval_seconds."""
    while True:
        await asyncio.sleep(settings.catalogue_refresh_interval_seconds)
        try:
            await warm_catalogue()
        except Exception:
            logger.warning("catalogue refresh failed", exc_info=True)
