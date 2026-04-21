"""FastAPI app — MCP bridge HTTP+SSE entry point for LibreChat.

Provides:
  GET  /health   — readiness probe (tools_loaded must be > 0)
  GET  /sse      — MCP SSE channel
  POST /messages — MCP message endpoint
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from app.config import settings

# AppInsights auto-instrumentation — no-op when connection string is absent
if settings.applicationinsights_connection_string:
    from azure.monitor.opentelemetry import configure_azure_monitor
    configure_azure_monitor(connection_string=settings.applicationinsights_connection_string)

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

import asyncio

from fastapi import FastAPI
from mcp.server.sse import SseServerTransport
from starlette.requests import Request
from starlette.routing import Route, Mount
from starlette.applications import Starlette

from app.catalogue_cache import warm_catalogue, refresh_loop
from app.server import mcp_server, create_initialization_options
from app.upload import upload_router
from app.upload_ui import upload_ui_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await warm_catalogue()
    task = asyncio.create_task(refresh_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


fastapi_app = FastAPI(title="MCP Bridge", version="1.0.0", lifespan=lifespan)


@fastapi_app.get("/health")
async def health():
    from app.catalogue_cache import get_tools_list
    tools = get_tools_list()
    return {"status": "ok", "tools_loaded": len(tools)}


fastapi_app.include_router(upload_router)
fastapi_app.include_router(upload_ui_router)


# ── MCP SSE transport ────────────────────────────────────────────────────────

sse_transport = SseServerTransport("/messages/")


# Starlette Route checks inspect.isfunction: functions → request_response(fn(request)->Response),
# class instances → ASGI app called directly as app(scope, receive, send).
# Wrapping as a class instance forces the ASGI path so connect_sse gets scope/receive/send.

class _SSEApp:
    async def __call__(self, scope, receive, send):
        async with sse_transport.connect_sse(scope, receive, send) as (read, write):
            await mcp_server.run(read, write, create_initialization_options())


class _MessagesApp:
    async def __call__(self, scope, receive, send):
        await sse_transport.handle_post_message(scope, receive, send)


# Mount MCP SSE routes as a Starlette sub-app on /
mcp_starlette = Starlette(routes=[
    Route("/sse", endpoint=_SSEApp()),
    Mount("/messages/", app=_MessagesApp()),
])

fastapi_app.mount("/", mcp_starlette)
