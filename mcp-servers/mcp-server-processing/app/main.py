import json
import logging
import re
from app.config import settings

if settings.applicationinsights_connection_string:
    from azure.monitor.opentelemetry import configure_azure_monitor
    configure_azure_monitor(connection_string=settings.applicationinsights_connection_string)


class _PrettyJsonFormatter(logging.Formatter):
    _JSON_RE = re.compile(r'(\{|\[)')

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        match = None
        for m in self._JSON_RE.finditer(text):
            match = m
        if match:
            prefix, tail = text[:match.start()], text[match.start():]
            try:
                return prefix + '\n' + json.dumps(json.loads(tail), indent=2, default=str)
            except (json.JSONDecodeError, ValueError):
                pass
        return text


_handler = logging.StreamHandler()
_handler.setFormatter(_PrettyJsonFormatter(
    fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
))
logging.basicConfig(level=settings.log_level.upper(), handlers=[_handler])

from fastapi import FastAPI
from app.router import router

app = FastAPI(title="MCP Processing Server", version="1.0.0")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "mcp-server-processing"}


app.include_router(router)
