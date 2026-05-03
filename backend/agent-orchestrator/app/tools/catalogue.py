"""Dynamic tool catalogue — fetches tool metadata from all MCP servers on every job."""
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


async def fetch_tool_catalogue() -> list[dict[str, Any]]:
    """Fetch the merged tool catalogue from all MCP servers.

    Always fetches fresh on every call — no caching — so that tool changes
    (server restarts, new tools) are picked up immediately for each job.

    Returns a flat list of tool descriptors, each containing:
      name, description, capability_tags, specialization,
      input_schema, output_schema, server
    """
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

    return all_tools


async def reset_analysis_rate_limiter() -> None:
    """Clear accumulated RPM-limiter timestamps on mcp-server-analysis.

    Called at the start of every job so that frontier-model rate limiting
    state from a previous job does not bleed into the new one.
    Best-effort: logs a warning and returns normally if the server is unavailable.
    """
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(f"{settings.mcp_analysis_url}/admin/reset-rate-limiter")
            resp.raise_for_status()
            logger.info("reset_analysis_rate_limiter: limiter cleared on mcp-server-analysis")
    except Exception as exc:
        logger.warning("reset_analysis_rate_limiter: could not reset limiter (non-fatal): %s", exc)


EXTERNAL_AGENT_ONLY_TOOLS: frozenset[str] = frozenset({"ingest_video"})


def filter_catalogue_for_frontend(catalogue: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove tools that are only for external agents.

    Called when the crew already has pre-processed video_urls (i.e. the job came
    through the normal frontend upload → preprocessing pipeline).  External agents
    that attach video files via chat do NOT have video_urls and need ingest_video
    to bootstrap the pipeline themselves.
    """
    return [t for t in catalogue if t.get("name") not in EXTERNAL_AGENT_ONLY_TOOLS]


_BOILERPLATE_INPUTS = {"job_id", "session_id"}


def _format_input_summary(input_schema: dict) -> str:
    """Compact required/optional input listing, excluding boilerplate job_id/session_id."""
    props = input_schema.get("properties", {})
    required = set(input_schema.get("required", []))
    req_parts: list[str] = []
    opt_parts: list[str] = []
    for name, spec in props.items():
        if name in _BOILERPLATE_INPUTS:
            continue
        ftype = spec.get("type", "any")
        if name in required:
            req_parts.append(f"{name} ({ftype})")
        else:
            default = spec.get("default")
            desc = spec.get("description", "")
            first_sentence = desc.split(".")[0].strip() if desc else ""
            part = f"{name} ({ftype}" + (f", default={default}" if default is not None else "") + ")"
            if first_sentence and len(first_sentence) <= 80:
                part += f": {first_sentence}"
            opt_parts.append(part)
    lines: list[str] = []
    if req_parts:
        lines.append(f"    inputs-required: {', '.join(req_parts)}")
    if opt_parts:
        lines.append(f"    inputs-optional: {'; '.join(opt_parts)}")
    return "\n".join(lines)


def _format_output_summary(output_schema: dict) -> str:
    """Blob content description and summary field names from the output schema."""
    props = output_schema.get("properties", {})
    lines: list[str] = []
    if "result_asset" in props:
        desc = props["result_asset"].get("description", "")
        if desc:
            lines.append(f"    output.result_asset: {desc}")
        summary_props = props.get("summary", {}).get("properties", {})
        if summary_props:
            fields = ", ".join(
                f"{k} ({v.get('type', 'any')})"
                for k, v in summary_props.items()
                if not str(k).startswith("#")
            )
            if fields:
                lines.append(f"    output.summary: {fields}")
    else:
        for name, spec in props.items():
            if name == "summary":
                continue
            ftype = spec.get("type", "any")
            desc = spec.get("description", "")
            part = f"    output.{name} ({ftype})"
            if desc:
                part += f": {desc}"
            lines.append(part)
    return "\n".join(lines)


def format_catalogue_for_planner(catalogue: list[dict[str, Any]]) -> str:
    """Return a human-readable tool listing with input/output schemas for the planner prompt."""
    lines: list[str] = ["Available tools:"]
    for tool in catalogue:
        tags = ", ".join(tool.get("capability_tags") or [])
        cost_tier = tool.get("cost_tier", "free")
        cost_note = tool.get("cost_note", "")
        cost_str = f"cost_tier={cost_tier}" + (f" ({cost_note})" if cost_note else "")
        entry = (
            f"  - {tool['name']} [{tool.get('server', '?')}]: {tool['description']}\n"
            f"    tags=[{tags}] specialization={tool.get('specialization', 'general')} {cost_str}"
        )
        if tool.get("input_schema"):
            input_lines = _format_input_summary(tool["input_schema"])
            if input_lines:
                entry += "\n" + input_lines
        if tool.get("output_schema"):
            output_lines = _format_output_summary(tool["output_schema"])
            if output_lines:
                entry += "\n" + output_lines
        lines.append(entry)
    return "\n".join(lines)
