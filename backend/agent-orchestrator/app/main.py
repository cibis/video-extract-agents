import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager
from app.config import settings

# Retry transient connection errors (DNS blips, TCP resets) in LiteLLM calls.
# num_retries applies to ServiceUnavailableError and APIConnectionError.
import litellm
litellm.num_retries = 3

# Configure Azure Monitor BEFORE FastAPI app creation
if settings.applicationinsights_connection_string:
    from azure.monitor.opentelemetry import configure_azure_monitor
    configure_azure_monitor(
        connection_string=settings.applicationinsights_connection_string
    )


class _PrettyJsonFormatter(logging.Formatter):
    """Log formatter that detects JSON blobs in messages and pretty-prints them."""

    _JSON_RE = re.compile(r'(\{|\[)')

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        # Find the last ': {' or ': [' pattern — pretty-print the JSON tail
        match = None
        for m in self._JSON_RE.finditer(text):
            match = m
        if match:
            prefix, tail = text[:match.start()], text[match.start():]
            try:
                obj = json.loads(tail)
                return prefix + '\n' + json.dumps(obj, indent=2, default=str)
            except (json.JSONDecodeError, ValueError):
                pass
        return text


_handler = logging.StreamHandler()
_handler.setFormatter(_PrettyJsonFormatter(
    fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
))
logging.basicConfig(level=settings.log_level.upper(), handlers=[_handler])

from fastapi import FastAPI  # noqa: E402
from app.server import router as run_router  # noqa: E402
from app.consumer import run_consumer  # noqa: E402

_logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(run_consumer())
    _logger.info("Service Bus consumer started as background task")
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        _logger.info("Service Bus consumer stopped")


app = FastAPI(title="Agent Orchestrator", version="1.0.0", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "agent-orchestrator"}


app.include_router(run_router)
