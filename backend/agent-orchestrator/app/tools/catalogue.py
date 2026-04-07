"""Dynamic tool catalogue — fetches tool metadata from all MCP servers at startup."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_MCP_SERVERS = [
    ("analysis", settings.mcp_analysis_url),
    ("processing", settings.mcp_processing_url),
]

_catalogue_cache: list[dict[str, Any]] | None = None


async def fetch_tool_catalogue(force_refresh: bool = False) -> list[dict[str, Any]]:
    """Fetch the merged tool catalogue from all MCP servers.

    Returns a flat list of tool descriptors, each containing:
      name, description, capability_tags, specialization,
      input_schema, output_schema, server
    """
    global _catalogue_cache
    if _catalogue_cache is not None and not force_refresh:
        return _catalogue_cache

    all_tools: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=10) as client:
        for server_name, base_url in _MCP_SERVERS:
            try:
                resp = await client.get(f"{base_url}/tools")
                resp.raise_for_status()
                tools = resp.json()
                for tool in tools:
                    tool["server"] = server_name
                    tool["server_url"] = base_url
                all_tools.extend(tools)
                logger.info("Fetched %d tools from %s", len(tools), server_name)
            except Exception as exc:
                logger.warning("Could not fetch tools from %s (%s): %s", server_name, base_url, exc)

    _catalogue_cache = all_tools
    return all_tools


EXTERNAL_AGENT_ONLY_TOOLS: frozenset[str] = frozenset({"ingest_video"})


def filter_catalogue_for_frontend(catalogue: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove tools that are only for external agents.

    Called when the crew already has pre-processed video_urls (i.e. the job came
    through the normal frontend upload → preprocessing pipeline).  External agents
    that attach video files via chat do NOT have video_urls and need ingest_video
    to bootstrap the pipeline themselves.
    """
    return [t for t in catalogue if t.get("name") not in EXTERNAL_AGENT_ONLY_TOOLS]


def format_catalogue_for_planner(catalogue: list[dict[str, Any]]) -> str:
    """Return a compact human-readable tool listing for inclusion in the planner prompt."""
    lines: list[str] = ["Available tools:"]
    for tool in catalogue:
        tags = ", ".join(tool.get("capability_tags") or [])
        cost_tier = tool.get("cost_tier", "free")
        cost_note = tool.get("cost_note", "")
        cost_str = f"cost_tier={cost_tier}"
        if cost_note:
            cost_str += f" ({cost_note})"
        lines.append(
            f"  - {tool['name']} [{tool.get('server', '?')}]: {tool['description']}\n"
            f"    tags=[{tags}] specialization={tool.get('specialization', 'general')} {cost_str}"
        )
    return "\n".join(lines)
