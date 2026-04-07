from app.config import settings

if settings.applicationinsights_connection_string:
    from azure.monitor.opentelemetry import configure_azure_monitor
    configure_azure_monitor(connection_string=settings.applicationinsights_connection_string)

import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager


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
from app.consumer import run_consumer

logger = logging.getLogger(__name__)


def _consumer_task_done(task: asyncio.Task) -> None:
    if not task.cancelled():
        exc = task.exception()
        if exc:
            logger.critical("Consumer task exited unexpectedly: %s", exc, exc_info=exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(run_consumer())
    task.add_done_callback(_consumer_task_done)
    logger.info("Service Bus consumer started")
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Preprocessing Worker", version="1.0.0", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "preprocessing-worker"}
