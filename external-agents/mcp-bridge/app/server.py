"""Standard MCP server — translates MCP protocol calls to custom SSE tool server calls.

Exposes the combined video-extraction tool catalogue (from mcp-server-analysis and
mcp-server-processing) as a standard MCP server compatible with LibreChat (SSE transport)
and Claude Desktop (stdio transport).
"""
from __future__ import annotations

import json
import logging
import pathlib
from typing import Any

from mcp.server import Server
from mcp import types
from mcp.server import NotificationOptions
from mcp.server.models import InitializationOptions

from app.bridge import invoke_custom_sse_tool
from app.catalogue_cache import get_tool_index, get_tools_list

logger = logging.getLogger(__name__)

mcp_server = Server("video-extraction-tools")


def _translate_tool(entry: dict[str, Any]) -> types.Tool:
    """Convert a custom SSE catalogue entry to a standard MCP Tool descriptor."""
    description = entry.get("description", "")
    cost_tier = entry.get("cost_tier", "free")
    tags = entry.get("capability_tags") or []

    # Enrich description with cost and capability hints for the agent
    if cost_tier == "frontier":
        description = f"[FRONTIER TOOL — API cost per batch] {description}"
    if tags:
        description = f"{description}\nCapabilities: {', '.join(tags)}"

    # input_schema passes through unchanged — already valid JSON Schema
    input_schema = entry.get("input_schema") or {"type": "object", "properties": {}}

    return types.Tool(
        name=entry["name"],
        description=description,
        inputSchema=input_schema,
    )


_GET_UPLOAD_URL_TOOL = types.Tool(
    name="get_upload_url",
    description=(
        "Generate a browser upload URL for this session. "
        "Present the returned upload_url as a clickable Markdown link so the user can open it "
        "in a new browser tab to upload video or other files. "
        "After the user confirms they have uploaded, call get_session_uploads to retrieve the blob URLs."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Session ID to tag uploads with."},
            "job_id": {"type": "string", "description": "Job ID to display on the upload page."},
        },
        "required": ["session_id"],
    },
)

_GET_SESSION_UPLOADS_TOOL = types.Tool(
    name="get_session_uploads",
    description=(
        "Return all files uploaded via the browser upload UI for this session. "
        "Call this after the user confirms they have uploaded their file(s). "
        "Each entry contains filename and blob_url — pass blob_url as source_url to ingest_video."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Session ID passed to get_upload_url."},
        },
        "required": ["session_id"],
    },
)


@mcp_server.list_tools()
async def list_tools() -> list[types.Tool]:
    tools = get_tools_list()
    logger.debug("list_tools: returning %d tools", len(tools))
    native = [_GET_UPLOAD_URL_TOOL, _GET_SESSION_UPLOADS_TOOL]
    return [_translate_tool(t) for t in tools] + native


@mcp_server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    # Native tools — handled locally, not forwarded to SSE tool servers
    if name == "get_upload_url":
        session_id = arguments["session_id"]
        job_id = arguments.get("job_id", "")
        params = f"session={session_id}"
        if job_id:
            params += f"&job={job_id}"
        upload_url = f"http://localhost:8301/upload-ui?{params}"
        result = {"upload_url": upload_url, "session_id": session_id}
        return [types.TextContent(type="text", text=json.dumps(result))]

    if name == "get_session_uploads":
        session_id = arguments["session_id"]
        from app import db
        uploads = await db.get_session_uploads(session_id)
        return [types.TextContent(type="text", text=json.dumps({"uploads": uploads}))]

    index = get_tool_index()
    entry = index.get(name)
    if entry is None:
        raise ValueError(f"Unknown tool: {name!r}. Available: {sorted(index)}")

    server_url = entry.get("_server_url", "")
    logger.info("call_tool: %s → %s", name, server_url)

    try:
        result = await invoke_custom_sse_tool(server_url, name, arguments)
    except RuntimeError as exc:
        from mcp.shared.exceptions import McpError
        from mcp.types import ErrorData
        raise McpError(ErrorData(code=-32603, message=str(exc))) from exc

    # # Remap internal azurite hostname to localhost so URLs are reachable from the host.
    # # This is external-agent specific — the main app uses azurite:10000 internally.
    # result_text = json.dumps(result, ensure_ascii=False).replace(
    #     "azurite:10000", "localhost:10000"
    # )
    
    result_text = json.dumps(result, ensure_ascii=False)

    return [types.TextContent(type="text", text=result_text)]


_PROMPTS_DIR = pathlib.Path(__file__).parent / "prompts"


def _load_prompt(filename: str) -> str:
    return (_PROMPTS_DIR / filename).read_text(encoding="utf-8")


@mcp_server.list_prompts()
async def list_prompts() -> list[types.Prompt]:
    return [
        types.Prompt(
            name="video-extraction-agent",
            description=(
                "System instructions for the autonomous video extraction agent — "
                "phases, tool selection, cost discipline, and robustness rules."
            ),
            arguments=[],
        )
    ]


@mcp_server.get_prompt()
async def get_prompt(name: str, arguments: dict[str, str] | None) -> types.GetPromptResult:
    if name != "video-extraction-agent":
        raise ValueError(f"Unknown prompt: {name!r}")
    text = _load_prompt("video-extraction-agent.md")
    return types.GetPromptResult(
        description="Video Extraction Agent — system instructions",
        messages=[
            types.PromptMessage(
                role="user",
                content=types.TextContent(type="text", text=text),
            )
        ],
    )


def create_initialization_options() -> InitializationOptions:
    return mcp_server.create_initialization_options(
        notification_options=NotificationOptions(),
        experimental_capabilities={},
    )
