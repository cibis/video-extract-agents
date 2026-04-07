"""Async SSE client for MCP tool servers."""
import json
import httpx
from typing import Any, Callable


async def invoke_mcp_tool(
    server_url: str,
    tool_name: str,
    payload: dict[str, Any],
    on_progress: Callable[[int, int | None, str], None] | None = None,
) -> dict[str, Any]:
    """
    Invoke an MCP tool via SSE transport.
    POST {server_url}/tools/{tool_name}/invoke
    Streams SSE events and returns the result from the final 'result' event.

    on_progress: optional sync callable(processed, total, unit) called for each
    'progress' SSE event. Caller is responsible for dispatching to the correct
    event loop (fire-and-forget) if DB writes are needed.
    """
    url = f"{server_url}/tools/{tool_name}/invoke"
    result: dict[str, Any] = {}

    async with httpx.AsyncClient(timeout=36000.0) as client:
        async with client.stream(
            "POST",
            url,
            json=payload,
            headers={"Accept": "text/event-stream", "Content-Type": "application/json"},
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]
                    try:
                        event = json.loads(data_str)
                        status = event.get("status")
                        if status == "progress":
                            if on_progress is not None:
                                on_progress(
                                    int(event.get("processed", 0)),
                                    int(event["total"]) if event.get("total") is not None else None,
                                    str(event.get("unit", "items")),
                                )
                        elif status == "result":
                            result = event.get("result", {})
                        elif status == "error":
                            raise RuntimeError(f"MCP tool error: {event.get('message')}")
                        elif status == "validation_error":
                            errors = "; ".join(event.get("errors", ["unknown validation error"]))
                            hint = event.get("hint", "")
                            raise RuntimeError(f"MCP tool validation error: {errors}. {hint}")
                    except json.JSONDecodeError:
                        continue

    return result
