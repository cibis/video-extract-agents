"""MCP tool router for mcp-server-processing."""
import asyncio
import inspect
import json
import logging
import queue as _queue_module
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from app.tool_registry import TOOLS, get_tool_catalogue
from app.validation import validate_tool_payload

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/tools")
async def list_tools():
    return get_tool_catalogue()


@router.post("/tools/{tool_name}/invoke")
async def invoke_tool(tool_name: str, request: Request):
    if tool_name not in TOOLS:
        raise HTTPException(status_code=404, detail=f"Tool not found: {tool_name}")

    payload = await request.json()
    tool_meta = TOOLS[tool_name]
    tool_fn = tool_meta["fn"]
    input_schema = tool_meta["input_schema"]

    async def sse_generator():
        yield f"data: {json.dumps({'status': 'processing', 'message': f'Invoking {tool_name}...'})}\n\n"

        errors = validate_tool_payload(tool_name, input_schema, payload)
        if errors:
            logger.warning("validation_error for %s: %s | payload keys: %s", tool_name, errors, list(payload.keys()))
            yield f"data: {json.dumps({'status': 'validation_error', 'tool': tool_name, 'errors': errors, 'hint': 'Correct the listed fields and retry this tool call.', 'input_schema': input_schema})}\n\n"
            return

        progress_q: _queue_module.Queue = _queue_module.Queue()

        def emit_progress(processed: int, total: int | None, unit: str) -> None:
            """Sync callback that tools call to report progress; safe from async context."""
            progress_q.put_nowait({"status": "progress", "processed": processed, "total": total, "unit": unit})

        try:
            sig = inspect.signature(tool_fn)
            if "progress_callback" in sig.parameters:
                task = asyncio.create_task(tool_fn(payload, progress_callback=emit_progress))
            else:
                task = asyncio.create_task(tool_fn(payload))

            while not task.done():
                while not progress_q.empty():
                    yield f"data: {json.dumps(progress_q.get_nowait())}\n\n"
                await asyncio.sleep(0.1)

            # Drain any final progress events emitted before task finished
            while not progress_q.empty():
                yield f"data: {json.dumps(progress_q.get_nowait())}\n\n"

            result = task.result()  # raises if the task raised
            yield f"data: {json.dumps({'status': 'result', 'result': result})}\n\n"
            yield f"data: {json.dumps({'status': 'done'})}\n\n"

        except Exception as exc:
            logger.error("error in tool %s: %s", tool_name, exc, exc_info=True)
            yield f"data: {json.dumps({'status': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(sse_generator(), media_type="text/event-stream")
