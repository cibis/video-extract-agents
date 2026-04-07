"""Model registry — resolves model identifiers to runnable clients.

Resolution order for a given model_id:
  1. Local disk model (for YOLO / Whisper etc.)
  2. Frontier API provider via LiteLLM (anthropic/…, openai/…, bedrock/…)
  3. HuggingFace Hub download
  4. MODEL_ALIASES map (short alias → full model string, then re-resolve)

tool_frontier_model is read from app_settings on every call so that changes
take effect immediately without restarting the container.
"""
from __future__ import annotations

import asyncio
import base64
import collections
import logging
import time
import traceback as _traceback
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_FRONTIER_PREFIXES = ("anthropic/", "openai/", "azure/", "bedrock/")
_LOCAL_PREFIXES = ("local/",)


class _AsyncRpmLimiter:
    """Async sliding-window rate limiter (60-second window).

    Counts calls within the last 60 seconds.  When the limit is reached,
    sleeps until the oldest call falls outside the window.
    """

    def __init__(self, max_rpm: int) -> None:
        self.max_rpm = max_rpm
        self._timestamps: collections.deque[float] = collections.deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            # Drop timestamps older than 60 s
            while self._timestamps and now - self._timestamps[0] >= 60.0:
                self._timestamps.popleft()
            if len(self._timestamps) >= self.max_rpm:
                # Wait until the oldest call exits the window
                wait = 60.0 - (now - self._timestamps[0]) + 0.05
                logger.info("tool rpm limit reached (%d/min) — waiting %.1f s", self.max_rpm, wait)
                await asyncio.sleep(wait)
                # Prune again after sleeping
                now = time.monotonic()
                while self._timestamps and now - self._timestamps[0] >= 60.0:
                    self._timestamps.popleft()
            self._timestamps.append(time.monotonic())


# Module-level limiter instance — recreated whenever the configured rpm changes.
_rpm_limiter: _AsyncRpmLimiter | None = None
_rpm_limiter_value: int | None = None


def _get_or_update_limiter(rpm_limit: int | None) -> _AsyncRpmLimiter | None:
    """Return the shared limiter, recreating it if the configured rpm changed."""
    global _rpm_limiter, _rpm_limiter_value
    if rpm_limit is None:
        _rpm_limiter = None
        _rpm_limiter_value = None
        return None
    if _rpm_limiter is None or _rpm_limiter_value != rpm_limit:
        _rpm_limiter = _AsyncRpmLimiter(rpm_limit)
        _rpm_limiter_value = rpm_limit
    return _rpm_limiter


class FrontierModelClient:
    """Thin async wrapper around LiteLLM for vision-capable frontier models."""

    def __init__(self, model_id: str, rpm_limit: int | None = None) -> None:
        self.model_id = model_id
        self.rpm_limit = rpm_limit

    async def complete(
        self,
        prompt: str,
        image_urls: list[str] | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Call the frontier model, optionally with image URLs for vision tasks."""
        try:
            import litellm  # lazy import — only needed when actually called
        except ImportError as exc:
            raise RuntimeError(
                "litellm is required for frontier model calls. "
                "Add it to pyproject.toml."
            ) from exc

        # Enforce RPM limit before making the API call
        limiter = _get_or_update_limiter(self.rpm_limit)
        if limiter is not None:
            await limiter.acquire()

        messages: list[dict] = []
        if image_urls:
            content: list[dict] = [{"type": "text", "text": prompt}]
            for url in image_urls:
                if url.startswith("data:"):
                    content.append({"type": "image_url", "image_url": {"url": url}})
                else:
                    # Fetch and base64-encode for providers that need it
                    b64 = await _fetch_as_base64(url)
                    content.append({"type": "image_url", "image_url": {"url": b64}})
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": prompt})

        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
            "max_tokens": max_tokens or settings.frontier_max_tokens,
            "temperature": 0,
        }
        if self.model_id.startswith("anthropic/") and settings.anthropic_api_key:
            kwargs["api_key"] = settings.anthropic_api_key
        elif self.model_id.startswith("openai/") and settings.openai_api_key:
            kwargs["api_key"] = settings.openai_api_key
        elif self.model_id.startswith("bedrock/"):
            if settings.aws_access_key_id:
                kwargs["aws_access_key_id"] = settings.aws_access_key_id
            if settings.aws_secret_access_key:
                kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
            if settings.aws_region_name:
                kwargs["aws_region_name"] = settings.aws_region_name

        # Retry on transient connection errors (e.g. intermittent DNS failures)
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                response = await litellm.acompletion(**kwargs)
                return response.choices[0].message.content or ""
            except litellm.APIConnectionError as exc:
                last_exc = exc
                wait = 2 ** attempt
                logger.warning(
                    "Frontier model connection error (attempt %d/3): %s — retrying in %ds",
                    attempt + 1, exc, wait,
                )
                await asyncio.sleep(wait)
        raise last_exc  # type: ignore[misc]

    async def call_vision_batch(
        self,
        prompt: str,
        image_urls: list[str],
        max_tokens: int | None = None,
    ) -> list[dict]:
        """Send all images in a single multi-image LiteLLM call and return a list of dicts.

        The model is instructed to respond with a JSON array whose length matches
        the number of images.  If the model returns fewer items than images, the
        missing positions are filled with {"error": "no_response"}.
        """
        import json as _json

        n = len(image_urls)
        full_prompt = (
            f"{prompt}\n\n"
            f"You are analysing {n} image(s) provided in order. "
            f"Respond ONLY with a valid JSON array of exactly {n} objects, "
            "one per image in the same order, with no extra text."
        )
        # Scale output budget proportionally to batch size so the model is never
        # cut off mid-JSON.  Clamp at the model ceiling; honour any explicit override
        # as a floor so callers can request more tokens if needed.
        scaled = n * settings.frontier_tokens_per_frame
        effective_max_tokens = min(scaled, settings.frontier_max_tokens)
        if max_tokens is not None:
            effective_max_tokens = max(effective_max_tokens, max_tokens)
        raw = await self.complete(full_prompt, image_urls=image_urls, max_tokens=effective_max_tokens)
        logger.debug("call_vision_batch raw response (n=%d): %s", n, raw)

        # Strip markdown code fences if present
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.rsplit("```", 1)[0].strip()

        _parse_error: str | None = None
        _parse_tb: str | None = None
        try:
            parsed = _json.loads(text)
            if not isinstance(parsed, list):
                parsed = [parsed]
        except Exception as exc:
            logger.warning("call_vision_batch: could not parse JSON response: %.200s", raw)
            _parse_error = f"{type(exc).__name__}: {exc}"
            _parse_tb = _traceback.format_exc()
            parsed = []

        # Pad / truncate to exactly n entries
        while len(parsed) < n:
            entry: dict[str, Any] = {"error": "no_response"}
            if _parse_error is not None:
                entry["error_detail"] = f"JSON parse failed: {_parse_error}"
                entry["traceback"] = _parse_tb
            parsed.append(entry)
        return parsed[:n]


_EXT_TO_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

_MAGIC_BYTES: list[tuple[bytes, str]] = [
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),  # checked further below
]


def _sniff_mime(url: str, data: bytes) -> str:
    """Infer MIME type from URL extension, then magic bytes, then default to image/jpeg."""
    from urllib.parse import urlparse
    import os

    ext = os.path.splitext(urlparse(url).path)[1].lower()
    if ext in _EXT_TO_MIME:
        return _EXT_TO_MIME[ext]

    for magic, mime in _MAGIC_BYTES:
        if data[:len(magic)] == magic:
            if mime == "image/webp" and data[8:12] != b"WEBP":
                continue
            return mime

    return "image/jpeg"


async def _fetch_as_base64(url: str) -> str:
    """Download a URL and return a data URI."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        data = resp.content
        if not content_type or not content_type.startswith("image/"):
            content_type = _sniff_mime(url, data)
        b64 = base64.b64encode(data).decode()
        return f"data:{content_type};base64,{b64}"


async def get_model_client(model_id: str) -> FrontierModelClient | str:
    """Resolve model_id and return a fresh client.

    Reads tool_frontier_model and tool_rpm_limit from app_settings on every call
    so changes take effect immediately without restarting the container.

    Returns:
        FrontierModelClient for frontier/API models.
        str (local model identifier) for local disk models.
    """
    from app.db import get_app_setting

    db_frontier_model, db_tool_rpm = await asyncio.gather(
        get_app_setting("tool_frontier_model"),
        get_app_setting("tool_rpm_limit"),
    )
    frontier_model = db_frontier_model or settings.tool_frontier_model

    # Resolve RPM limit: DB value overrides settings; empty/missing = no limit
    tool_rpm_limit: int | None
    if db_tool_rpm is not None and db_tool_rpm != "":
        try:
            _v = int(db_tool_rpm)
            tool_rpm_limit = _v if _v > 0 else None
        except (ValueError, TypeError):
            tool_rpm_limit = settings.tool_rpm_limit
    else:
        tool_rpm_limit = settings.tool_rpm_limit

    # Build alias map fresh from DB value
    aliases = dict(settings.get_model_aliases())
    aliases["claude-vision"] = frontier_model

    resolved = aliases.get(model_id, model_id)

    if any(resolved.startswith(p) for p in _FRONTIER_PREFIXES):
        return FrontierModelClient(resolved, rpm_limit=tool_rpm_limit)
    elif any(resolved.startswith(p) for p in _LOCAL_PREFIXES):
        return resolved
    else:
        logger.info("Unknown model prefix for %s; treating as local/HF identifier", resolved)
        return resolved
