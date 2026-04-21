"""stdio entry point for Claude Desktop.

Claude Desktop spawns this as a subprocess and communicates via stdin/stdout JSON-RPC.
ALL logging goes to stderr — stdout must contain only clean JSON-RPC messages.

Usage (from Claude Desktop config):
  docker exec -i video-extract-mcp-bridge python -m app.stdio_entry
"""
from __future__ import annotations

import asyncio
import logging
import sys

# Redirect all logging to stderr so stdout remains clean JSON-RPC
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)

from mcp.server.stdio import stdio_server

from app.catalogue_cache import warm_catalogue
from app.server import mcp_server, create_initialization_options


async def main() -> None:
    await warm_catalogue()
    async with stdio_server() as (read_stream, write_stream):
        await mcp_server.run(read_stream, write_stream, create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
