"""CrewAI BaseTool wrappers that call MCP tool servers over SSE transport."""
from __future__ import annotations

import asyncio
import json
import logging
import queue as _queue_module
import uuid
from typing import Any, Optional, Type
from urllib.parse import urlparse

from crewai.tools import BaseTool
from pydantic import BaseModel, ConfigDict, PrivateAttr, create_model

from app.tools.mcp_client import invoke_mcp_tool
from app.litellm_callbacks import _thread_local

logger = logging.getLogger(__name__)

# Set by crew.py before kickoff; receives job_log metadata from MCP results
_mcp_job_log_queue: _queue_module.Queue | None = None


def set_mcp_job_log_queue(q: _queue_module.Queue | None) -> None:
    global _mcp_job_log_queue
    _mcp_job_log_queue = q


def _service_name_from_url(url: str) -> str:
    """Extract the hostname from a server URL to use as the service name.

    e.g. 'http://mcp-server-analysis:8100' -> 'mcp-server-analysis'
    """
    try:
        return urlparse(url).hostname or url
    except Exception:
        return url


_JSON_SCHEMA_TYPE_MAP: dict[str, type] = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}

# Keys injected by CrewAI internals that must not be forwarded to MCP servers.
_CREWAI_INTERNAL_KEYS = {"metadata", "security_context"}


def _make_args_schema(input_schema: dict[str, Any]) -> type[BaseModel]:
    """Build a per-tool Pydantic model from a JSON Schema input_schema.

    Declaring real fields causes CrewAI to show 'Tool Arguments: {field: type, ...}'
    in the LLM prompt, so the model knows what kwargs to send. A shared empty
    schema (_AnyInput with no fields) shows 'Tool Arguments: {}', which causes
    the LLM to omit all arguments.

    extra='allow' lets CrewAI's internally-injected security_context pass
    validation without surfacing as an error.
    """
    props = input_schema.get("properties") or {}
    required = input_schema.get("required") or []
    fields: dict[str, Any] = {}
    for name, meta in props.items():
        py_type = _JSON_SCHEMA_TYPE_MAP.get(meta.get("type", "string"), str)
        if name in required:
            fields[name] = (py_type, ...)
        else:
            fields[name] = (Optional[py_type], None)
    return create_model(
        "ToolArgs",
        **fields,
        __config__=ConfigDict(extra="allow"),
    )


class McpTool(BaseTool):
    """Generic CrewAI tool wrapper for a single MCP tool.

    Called by CrewAI agents in executor threads (no running event loop),
    so asyncio.run() is safe here.
    """

    name: str
    description: str
    args_schema: Type[BaseModel] = _make_args_schema({})
    _tool_name: str = PrivateAttr()
    _server_url: str = PrivateAttr()

    def __init__(self, tool_name: str, server_url: str, description: str, args_schema: Type[BaseModel], **kwargs):
        super().__init__(name=tool_name, description=description, args_schema=args_schema, **kwargs)
        self._tool_name = tool_name
        self._server_url = server_url

    def _run(self, **kwargs: Any) -> str:  # type: ignore[override]
        """Synchronously invoke the MCP tool and return JSON result string."""
        call_group_id = str(uuid.uuid4())
        try:
            payload: dict[str, Any] = {
                k: v for k, v in kwargs.items()
                if k not in _CREWAI_INTERNAL_KEYS and v is not None
            }

            logger.info("MCP tool call: %s payload=%s", self._tool_name, str(payload)[:500])

            try:
                input_message = json.dumps(payload, ensure_ascii=False)
            except Exception:
                input_message = str(payload)

            counter = getattr(_thread_local, "seq_counter", None)
            seq_input = next(counter) if counter is not None else 0
            job_id = getattr(_thread_local, "job_id", None)
            session_id = getattr(_thread_local, "session_id", None)
            _loop = getattr(_thread_local, "event_loop", None)
            service = _service_name_from_url(self._server_url)

            # --- Write Input row IMMEDIATELY (before tool executes) ---
            self._write_log_row({
                "job_id": job_id,
                "session_id": session_id,
                "service_name": service,
                "log_type": "tool_call",
                "model_id": None,
                "tool_name": self._tool_name,
                "message": input_message,
                "message_type": "Input",
                "call_group_id": call_group_id,
                "sequence_num": seq_input,
                "error_text": None,
            })

            # --- Insert initial tool_progress row ---
            if _loop is not None and _loop.is_running() and job_id:
                from app.db import insert_tool_progress
                asyncio.run_coroutine_threadsafe(
                    insert_tool_progress(call_group_id, job_id, self._tool_name), _loop
                )

            # --- Build sync on_progress callback (fire-and-forget to main loop) ---
            on_progress_cb = None
            if _loop is not None and _loop.is_running():
                _captured_cgid = call_group_id
                _captured_loop = _loop

                def on_progress_cb(processed: int, total: int | None, unit: str) -> None:
                    from app.db import upsert_tool_progress
                    asyncio.run_coroutine_threadsafe(
                        upsert_tool_progress(_captured_cgid, processed, total, unit),
                        _captured_loop,
                    )

            # --- Invoke tool (blocking asyncio.run in executor thread) ---
            result = asyncio.run(
                invoke_mcp_tool(self._server_url, self._tool_name, payload, on_progress_cb)
            )
            logger.info("MCP tool result: %s -> %s", self._tool_name, str(result)[:500])

            # --- Mark tool_progress completed ---
            if _loop is not None and _loop.is_running() and job_id:
                from app.db import complete_tool_progress
                asyncio.run_coroutine_threadsafe(
                    complete_tool_progress(call_group_id, success=True), _loop
                )

            # Extract frontier model log metadata if present (e.g. from analyze_scene)
            job_log_entry: dict | None = None
            if _mcp_job_log_queue is not None and isinstance(result, dict):
                job_log_entry = result.pop("_job_log", None)

            try:
                output_message = json.dumps(result, ensure_ascii=False)
            except Exception:
                output_message = str(result)

            seq_output = next(counter) if counter is not None else 0

            # --- Write Output row AFTER tool completes ---
            self._write_log_row({
                "job_id": job_id,
                "session_id": session_id,
                "service_name": service,
                "log_type": "tool_call",
                "model_id": None,
                "tool_name": self._tool_name,
                "message": output_message,
                "message_type": "Output",
                "call_group_id": call_group_id,
                "sequence_num": seq_output,
                "error_text": None,
            })

            # Log any frontier model call embedded in the result
            if job_log_entry:
                job_id = job_log_entry.get("job_id") or job_id
                session_id = job_log_entry.get("session_id") or session_id
                # Stamp frontier log with current sequence numbers
                frontier_group_id = str(uuid.uuid4())
                seq_f_in = next(counter) if counter is not None else 0
                seq_f_out = next(counter) if counter is not None else 0
                base = dict(
                    job_id=job_id,
                    session_id=session_id,
                    service_name=job_log_entry.get("service_name", "unknown"),
                    log_type=job_log_entry.get("log_type", "llm_call"),
                    model_id=job_log_entry.get("model_id"),
                    tool_name=job_log_entry.get("tool_name"),
                    agent_role=job_log_entry.get("agent_role"),
                    task_name=job_log_entry.get("task_name"),
                    call_group_id=frontier_group_id,
                    error_text=job_log_entry.get("error_text"),
                )
                self._write_log_row({**base, "message": job_log_entry.get("input_data"), "message_type": "Input", "sequence_num": seq_f_in})
                self._write_log_row({**base, "message": job_log_entry.get("output_data"), "message_type": "Output", "sequence_num": seq_f_out})

            return json.dumps(result)
        except Exception as exc:
            error_text = str(exc)
            logger.error("MCP tool %s failed: %s", self._tool_name, exc)
            # Mark progress failed
            try:
                _loop = getattr(_thread_local, "event_loop", None)
                if _loop is not None and _loop.is_running():
                    from app.db import complete_tool_progress
                    asyncio.run_coroutine_threadsafe(
                        complete_tool_progress(call_group_id, success=False), _loop
                    )
            except Exception:
                pass
            counter = getattr(_thread_local, "seq_counter", None)
            seq_err = next(counter) if counter is not None else 0
            self._enqueue_error_row(
                call_group_id=call_group_id,
                seq_num=seq_err,
                error_text=error_text,
            )
            return json.dumps({"error": error_text})

    def _write_log_row(self, entry: dict) -> None:
        """Write a single log entry to the DB in real-time, falling back to the queue."""
        _loop = getattr(_thread_local, "event_loop", None)
        if _loop is not None and _loop.is_running():
            from app.db import record_job_log  # local import avoids top-level circular risk
            asyncio.run_coroutine_threadsafe(record_job_log(**entry), _loop)
        elif _mcp_job_log_queue is not None:
            _mcp_job_log_queue.put(entry)

    def _enqueue_two_rows(
        self,
        log_type: str,
        call_group_id: str,
        input_message: str | None,
        seq_input: int,
        output_message: str | None,
        seq_output: int,
        error_text: str | None,
    ) -> None:
        """Write Input then Output rows to the DB (or queue as fallback)."""
        try:
            job_id = getattr(_thread_local, "job_id", None)
            session_id = getattr(_thread_local, "session_id", None)
            service = _service_name_from_url(self._server_url)
            self._write_log_row({
                "job_id": job_id,
                "session_id": session_id,
                "service_name": service,
                "log_type": log_type,
                "model_id": None,
                "tool_name": self._tool_name,
                "message": input_message if input_message else None,
                "message_type": "Input",
                "call_group_id": call_group_id,
                "sequence_num": seq_input,
                "error_text": None,
            })
            self._write_log_row({
                "job_id": job_id,
                "session_id": session_id,
                "service_name": service,
                "log_type": log_type,
                "model_id": None,
                "tool_name": self._tool_name,
                "message": output_message if output_message else None,
                "message_type": "Output",
                "call_group_id": call_group_id,
                "sequence_num": seq_output,
                "error_text": error_text,
            })
        except Exception:
            logger.exception("_enqueue_two_rows: failed to write log entries — ignoring")

    def _enqueue_error_row(
        self,
        call_group_id: str,
        seq_num: int,
        error_text: str,
    ) -> None:
        """Write a single Error row to the DB (or queue as fallback)."""
        try:
            job_id = getattr(_thread_local, "job_id", None)
            session_id = getattr(_thread_local, "session_id", None)
            self._write_log_row({
                "job_id": job_id,
                "session_id": session_id,
                "service_name": _service_name_from_url(self._server_url),
                "log_type": "error",
                "model_id": None,
                "tool_name": self._tool_name,
                "message": error_text,
                "message_type": "Error",
                "call_group_id": call_group_id,
                "sequence_num": seq_num,
                "error_text": error_text,
            })
        except Exception:
            logger.exception("_enqueue_error_row: failed to write log entry — ignoring")


def _build_description(tool: dict[str, Any]) -> str:
    """Build a tool description string that includes the input schema."""
    base = tool.get("description", tool["name"])
    schema = tool.get("input_schema") or {}
    props = schema.get("properties") or {}
    required = schema.get("required") or []

    if not props:
        return base

    field_lines = []
    for field, meta in props.items():
        req = " (required)" if field in required else " (optional)"
        ftype = meta.get("type", "any")
        fdesc = meta.get("description", "")
        field_lines.append(f"  {field}: {ftype}{req} — {fdesc}" if fdesc else f"  {field}: {ftype}{req}")

    schema_text = "\n".join(field_lines)
    return f"{base}\nInput fields:\n{schema_text}"


def build_crewai_tools(catalogue: list[dict[str, Any]]) -> list[McpTool]:
    """Create one McpTool per catalogue entry."""
    tools = []
    for entry in catalogue:
        tool_name = entry.get("name", "")
        server_url = entry.get("server_url", "")
        if not tool_name or not server_url:
            continue
        description = _build_description(entry)
        schema = _make_args_schema(entry.get("input_schema") or {})
        tools.append(McpTool(
            tool_name=tool_name,
            server_url=server_url,
            description=description,
            args_schema=schema,
        ))
    return tools
