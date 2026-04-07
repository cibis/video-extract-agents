"""Async SSE client for the custom HTTP+SSE MCP tool servers.

Mirrors backend/agent-orchestrator/app/tools/mcp_client.py — ported for use in the bridge.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def fetch_catalogue(base_url: str) -> list[dict[str, Any]]:
    """Fetch the tool catalogue from one MCP server and tag each entry with _server_url."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{base_url}/tools")
        resp.raise_for_status()
        tools: list[dict] = resp.json()
    for tool in tools:
        tool["_server_url"] = base_url
    return tools


async def invoke_custom_sse_tool(
    server_url: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Invoke a tool on a custom HTTP+SSE MCP server.

    POST {server_url}/tools/{tool_name}/invoke
    Streams SSE events and returns the result from the final 'result' event.
    Raises RuntimeError on 'error' or 'validation_error' events.
    """
    url = f"{server_url}/tools/{tool_name}/invoke"
    result: dict[str, Any] = {}

    async with httpx.AsyncClient(timeout=settings.tool_call_timeout_seconds) as client:
        async with client.stream(
            "POST",
            url,
            json=arguments,
            headers={"Accept": "text/event-stream", "Content-Type": "application/json"},
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                status = event.get("status")
                if status == "result":
                    result = event.get("result", {})
                elif status == "error":
                    raise RuntimeError(f"MCP tool error: {event.get('message')}")
                elif status == "validation_error":
                    errors = "; ".join(event.get("errors", ["unknown validation error"]))
                    hint = event.get("hint", "")
                    raise RuntimeError(f"MCP tool validation error: {errors}. {hint}")

    return result
