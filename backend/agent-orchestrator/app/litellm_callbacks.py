"""LiteLLM model call tracking via direct monkey-patching of litellm.completion.

We cannot use litellm.success_callback because CrewAI 0.203.2 resets it via
LLM.set_env_callbacks() every time an Agent is instantiated, wiping any
registered callbacks before kickoff() even runs.

Instead, _kickoff_with_context() in crew.py calls wrap_litellm_completion()
which temporarily replaces litellm.completion with a thin wrapper that records
every call — success or failure — to a thread-safe queue. The original function
is restored in the finally block regardless of outcome.

Thread-safety: queue.Queue is thread-safe. The monkey-patch is installed and
removed within a single executor-thread call so concurrent runs each get their
own wrapper installed/removed in the same thread as their crew.kickoff().

Real-time writes: when an event loop is registered via set_loop(), each log
entry is written directly to the DB via asyncio.run_coroutine_threadsafe()
instead of being queued.  The queue acts as a fallback if no loop is set.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import queue
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Generator

from app.config import settings
from app.utils import strip_code_fences, convert_function_calls_to_react, convert_json_wrapped_react

logger = logging.getLogger(__name__)


def _patch_crewai_parser() -> None:
    """Patch crewai.agents.parser._safe_repair_json to treat \\nObservation: as end of tool input.

    Some models (e.g. Amazon Nova Lite on Bedrock) append \\nObservation: {"
    after Action Input instead of stopping cleanly (stopReason: end_turn, not
    stop_sequence). CrewAI's greedy ACTION_INPUT_REGEX (re.DOTALL) captures the
    suffix; repair_json() then wraps the two partial JSON objects into an array,
    which fails dict validation → "Action Input is not a valid key, value
    dictionary" → infinite retry loop up to max_iter.

    Observation is always written by the executor, never by the model, so
    truncating at \\nObservation: is always correct for any model.
    """
    import crewai.agents.parser as _parser
    _orig = _parser._safe_repair_json

    def _patched(tool_input: str) -> str:
        for sentinel in ("\nObservation:", "\nAction:"):
            idx = tool_input.find(sentinel)
            if idx != -1:
                tool_input = tool_input[:idx]
        return _orig(tool_input)

    _parser._safe_repair_json = _patched
    logger.debug("_patch_crewai_parser: _safe_repair_json patched to strip \\nObservation: and \\nAction: suffixes")


_patch_crewai_parser()


class ToolRetryLimitExceeded(Exception):
    """Raised when a tool exceeds tool_max_retry_limit consecutive ToolUsageErrors."""


class LlmCyclingLimitExceeded(Exception):
    """Raised when tool_max_retry_limit consecutive LLM calls produce no successful tool use."""


def _is_failed_tool_result(calling, result: str) -> bool:
    """Return True when a ToolUsage.use() call represents a failure.

    Three failure categories, each with a different signature:

    1. Parse error — `calling` is not a ToolCalling/InstructorToolCalling
       (e.g. ToolUsageError from _safe_repair_json / _validate_tool_input).

    2. Execution error returning plain text — `calling` IS a ToolCalling but
       the result is not JSON. Covers:
       - CrewAI's own _check_tool_repeated_usage() which returns a localised
         plain-text string without ever calling tool._run().
       - _select_tool() exception caught by use() and returned as plain text.

    3. MCP execution error — result IS JSON but carries a non-null "error" key
       (McpTool._run()'s exception handler returns {"error": "..."}).
    """
    from crewai.tools.tool_calling import ToolCalling, InstructorToolCalling

    if not isinstance(calling, (ToolCalling, InstructorToolCalling)):
        return True  # parse error
    try:
        parsed = json.loads(result)
        if isinstance(parsed, dict) and parsed.get("error") is not None:
            return True  # MCP {"error": "..."} response
    except (json.JSONDecodeError, TypeError, ValueError):
        return True  # plain text = execution error
    return False


@contextmanager
def guard_tool_usage_errors(limit: int) -> Generator[None, None, None]:
    """Raise after `limit` consecutive tool failures for the same tool per job.

    ToolUsageError is normally returned as a string observation, so CrewAI's
    max_retry_limit never fires and the agent loops up to max_iter times. This
    context manager patches ToolUsage.use() for the duration of crew.kickoff()
    to count per-tool consecutive failures and raise ToolRetryLimitExceeded
    once the limit is reached — breaking the loop immediately.

    Failures detected (see _is_failed_tool_result):
    - Parse errors (calling not a ToolCalling)
    - Plain-text execution errors (CrewAI repeated-usage guard, _select_tool)
    - MCP JSON errors ({"error": "..."} responses)

    The counter resets for a tool on any successful call. Counts persist across
    Agent.execute_task() retries (the dict lives in the closure), so a stuck
    tool fails the task quickly on every subsequent retry as well.
    """
    from crewai.tools.tool_usage import ToolUsage

    _orig_use = ToolUsage.use
    _counts: dict[str, int] = {}  # tool_name → consecutive failure count

    def _guarded_use(self, calling, tool_string: str) -> str:
        tool_name = (
            getattr(calling, "tool_name", None)
            or getattr(getattr(self, "action", None), "tool", None)
            or "unknown"
        )
        result = _orig_use(self, calling, tool_string)

        if _is_failed_tool_result(calling, result):
            _counts[tool_name] = _counts.get(tool_name, 0) + 1
            in_recovery = getattr(_thread_local, "recovery_model_active", False)
            logger.warning(
                "guard_tool_usage_errors: tool '%s' failure %d/%d%s — %s",
                tool_name, _counts[tool_name], limit,
                " [recovery model]" if in_recovery else "",
                str(result)[:200],
            )
            if _counts[tool_name] >= limit:
                if in_recovery:
                    # Already on planner model and still hitting the limit → abort
                    raise ToolRetryLimitExceeded(
                        f"Tool '{tool_name}' failed {_counts[tool_name]} consecutive "
                        f"times with planner model (limit: {limit}). Last error: {result}"
                    )
                # First time hitting limit: switch to planner model and reset counters
                _thread_local.recovery_model_active = True
                _counts.clear()
                if hasattr(_thread_local, "llm_cycle_count"):
                    _thread_local.llm_cycle_count = 0
                logger.info(
                    "guard_tool_usage_errors: tool '%s' hit retry limit (%d) — "
                    "switching to planner model and resetting counters",
                    tool_name, limit,
                )
        else:
            if tool_name in _counts:
                logger.debug("guard_tool_usage_errors: tool '%s' succeeded — resetting counter", tool_name)
            _counts.pop(tool_name, None)  # reset per-tool counter on successful call
            # Reset global LLM cycling counter — a tool succeeded
            if hasattr(_thread_local, "llm_cycle_count"):
                _thread_local.llm_cycle_count = 0

        return result

    ToolUsage.use = _guarded_use
    try:
        yield
    finally:
        ToolUsage.use = _orig_use


def _strip_response_fences(result) -> None:
    """Normalise LLM response content to CrewAI ReAct format in-place.

    Three transformations are applied before CrewAI's parser sees the content:

    1. Claude Haiku 4.5 overrides the ReAct text instructions and emits tool
       calls as <function_calls>[{"tool_name":...,"arguments":...}]
       </function_calls> XML.  convert_function_calls_to_react() rewrites this
       to the expected Action: / Action Input: format.

    2. Weaker models (e.g. Amazon Nova Lite) wrap their ReAct output in
       ```...``` code blocks.  strip_code_fences() removes the delimiters so
       ast.literal_eval() can parse the Action Input JSON cleanly.

    3. Amazon Nova Lite occasionally wraps its entire Thought/Action/Action
       Input (or Thought/Final Answer) response as a JSON object instead of
       emitting plain text.  convert_json_wrapped_react() detects this by
       looking for an "Action" or "Final Answer" key in the top-level object
       and rewrites it to the plain-text ReAct format.  Applied after fence
       stripping so that JSON-inside-a-code-block is also covered.
    """
    if result is not None and getattr(result, "choices", None):
        content = result.choices[0].message.content or ""
        if "<function_calls>" in content:
            content = convert_function_calls_to_react(content)
        if "```" in content:
            content = strip_code_fences(content)
        if content.strip().startswith("{"):
            content = convert_json_wrapped_react(content)
        result.choices[0].message.content = content


# Thread-safe queue for job log data collected during crew.kickoff()
_pending_logs: queue.Queue = queue.Queue()

# Per-thread job context set inside the executor thread before kickoff
_thread_local = threading.local()


def set_job_context(job_id: str, session_id: str | None, user_id: str | None = None) -> None:
    """Call from the executor thread (inside the kickoff lambda) before kickoff."""
    _thread_local.job_id = job_id
    _thread_local.session_id = session_id
    _thread_local.user_id = user_id


def clear_job_context() -> None:
    """Call from the executor thread after kickoff completes."""
    _thread_local.job_id = None
    _thread_local.session_id = None
    _thread_local.user_id = None


def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Register the running event loop for this executor thread.

    Called by _kickoff_with_context() so that _record_completion (and any other
    callbacks sharing this _thread_local) can schedule real-time DB writes via
    asyncio.run_coroutine_threadsafe().
    """
    _thread_local.event_loop = loop


def clear_loop() -> None:
    """Clear the event loop reference after kickoff completes."""
    _thread_local.event_loop = None


def _response_has_tool_call(result) -> bool:
    """
    Best-effort detection of whether an LLM response contains a tool/function call.

    Supports:
    - OpenAI / Azure (tool_calls, function_call)
    - Anthropic (tool_use blocks)
    - LiteLLM normalized objects
    - Raw dict responses
    - Fallback: JSON/text heuristics
    """

    def _get(obj, key, default=None):
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    def _has_tool_in_message(msg) -> bool:
        if not msg:
            return False

        # --- 1. OpenAI / LiteLLM modern ---
        if _get(msg, "tool_calls"):
            return True

        # --- 2. OpenAI legacy ---
        if _get(msg, "function_call"):
            return True

        # --- 3. Anthropic / Claude (content blocks) ---
        content = _get(msg, "content")

        if isinstance(content, list):
            for block in content:
                # dict-style block
                if isinstance(block, dict):
                    if block.get("type") in ("tool_use", "tool_call"):
                        return True
                    if block.get("name") and block.get("input"):
                        return True

                # object-style block
                else:
                    if getattr(block, "type", None) in ("tool_use", "tool_call"):
                        return True
                    if getattr(block, "name", None) and getattr(block, "input", None):
                        return True

        # --- 4. Some providers put tool info directly on message ---
        if _get(msg, "tool_name") or _get(msg, "tool"):
            return True

        # --- 5. Text fallback (weak but useful) ---
        text = _get(msg, "content")
        if isinstance(text, str):
            # common structured outputs
            if '"tool_calls"' in text:
                return True
            if '"function_call"' in text:
                return True
            if '"name":' in text and '"arguments"' in text:
                return True

        return False

    try:
        if result is None:
            return False

        # --- OpenAI / LiteLLM style ---
        choices = _get(result, "choices")
        if choices:
            first = choices[0]

            msg = _get(first, "message") or _get(first, "delta")
            if _has_tool_in_message(msg):
                return True

        # --- Anthropic top-level content ---
        content = _get(result, "content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") in ("tool_use", "tool_call"):
                        return True
                else:
                    if getattr(block, "type", None) in ("tool_use", "tool_call"):
                        return True

        # --- Some providers embed message directly ---
        if _has_tool_in_message(result):
            return True

        return False

    except Exception:
        # Never break agent loop due to detection failure
        return False


@contextmanager
def wrap_litellm_completion() -> Generator[None, None, None]:
    """Context manager that monkey-patches litellm.completion for the duration.

    Records every call (success and failure) to _pending_logs.
    Must be entered from the executor thread that runs crew.kickoff().
    """
    import litellm as _litellm

    _original = _litellm.completion

    def _record_completion(*args, **kwargs):
        # Recovery mechanism: when guard_tool_usage_errors activates recovery mode,
        # override the model with the planner model for all subsequent LLM calls
        # and enforce the planner_rpm_limit using a sliding 60-second window.
        if getattr(_thread_local, "recovery_model_active", False):
            _recovery_model = getattr(_thread_local, "recovery_model", None)
            if _recovery_model:
                kwargs["model"] = _recovery_model
            _rpm_limit = getattr(_thread_local, "planner_rpm_limit", None)
            if _rpm_limit:
                if getattr(_thread_local, "recovery_call_times", None) is None:
                    _thread_local.recovery_call_times = collections.deque()
                _call_times = _thread_local.recovery_call_times
                _now = time.monotonic()
                while _call_times and _call_times[0] < _now - 60.0:
                    _call_times.popleft()
                if len(_call_times) >= _rpm_limit:
                    _sleep = 60.0 - (_now - _call_times[0]) + 0.05
                    if _sleep > 0:
                        logger.debug(
                            "_record_completion: recovery RPM limit (%d) — sleeping %.1f s",
                            _rpm_limit, _sleep,
                        )
                        time.sleep(_sleep)
                _thread_local.recovery_call_times.append(time.monotonic())

        # Defensive guard: Anthropic rejects requests whose messages array ends
        # with role=assistant ("assistant message prefill"). This can happen when
        # CrewAI's ReAct retry logic fails to append a closing user message after
        # a tool error observation. Append a minimal user turn to fix it silently.
        messages = kwargs.get("messages")
        if isinstance(messages, list) and messages and messages[-1].get("role") == "assistant":
            logger.debug("_record_completion: messages end with assistant — appending user turn")
            kwargs["messages"] = messages + [{"role": "user", "content": "Continue."}]

        result = None
        exc = None
        try:
            result = _original(*args, **kwargs)
            _strip_response_fences(result)
            return result
        except Exception as e:
            # Some Bedrock models reject the stopSequences field that CrewAI
            # injects for ReAct agents. Strip it and retry once.
            if "stopSequences" in str(e) and "stop" in kwargs:
                logger.debug(
                    "_record_completion: model rejected stopSequences — retrying without stop parameter"
                )
                kwargs_retry = {k: v for k, v in kwargs.items() if k != "stop"}
                try:
                    result = _original(*args, **kwargs_retry)
                    _strip_response_fences(result)
                    return result
                except Exception as e2:
                    exc = e2
                    raise

            # # Transient InternalServerError (e.g. Docker DNS blip resolving
            # # api.anthropic.com): retry up to 3 times with 5 s sleep.
            # # litellm.num_retries only covers ServiceUnavailableError /
            # # APIConnectionError; InternalServerError is a separate class.
            # import litellm as _litellm_inner
            # if isinstance(e, _litellm_inner.InternalServerError):
            #     for attempt in range(1, 4):
            #         logger.warning(
            #             "_record_completion: InternalServerError (attempt %d/3) — "
            #             "sleeping 5 s before retry: %s",
            #             attempt, e,
            #         )
            #         time.sleep(5)
            #         try:
            #             result = _original(*args, **kwargs)
            #             _strip_response_fences(result)
            #             return result
            #         except _litellm_inner.InternalServerError as e_retry:
            #             if attempt == 3:
            #                 exc = e_retry
            #                 raise
            #         except Exception as e_retry:
            #             exc = e_retry
            #             raise

            exc = e
            raise
        finally:
            try:
                job_id = getattr(_thread_local, "job_id", None)
                if not job_id:
                    return
                session_id = getattr(_thread_local, "session_id", None)
                counter = getattr(_thread_local, "seq_counter", None)
                model_id = kwargs.get("model", args[0] if args else "unknown")
                call_group_id = str(uuid.uuid4())

                messages = kwargs.get("messages", args[1] if len(args) > 1 else [])
                try:
                    input_message = json.dumps(messages, ensure_ascii=False)
                except Exception:
                    input_message = str(messages)

                seq_input = next(counter) if counter is not None else 0

                seq_output = next(counter) if counter is not None else 0
                if exc is not None:
                    out_message = f"ERROR: {exc}"
                    out_type = "Error"
                    out_error = str(exc)
                else:
                    out_message = ""
                    if result is not None and getattr(result, "choices", None):
                        out_message = result.choices[0].message.content or ""
                    out_type = "Output"
                    out_error = None

                _loop = getattr(_thread_local, "event_loop", None)
                if _loop is not None and _loop.is_running():
                    # Real-time path: write directly to DB without buffering
                    from app.db import record_job_log  # local import avoids top-level circular risk
                    asyncio.run_coroutine_threadsafe(
                        record_job_log(
                            job_id=job_id,
                            session_id=session_id,
                            service_name=settings.service_name,
                            log_type="llm_call",
                            model_id=model_id,
                            tool_name=None,
                            message=input_message,
                            message_type="Input",
                            call_group_id=call_group_id,
                            sequence_num=seq_input,
                            error_text=None,
                        ),
                        _loop,
                    )
                    asyncio.run_coroutine_threadsafe(
                        record_job_log(
                            job_id=job_id,
                            session_id=session_id,
                            service_name=settings.service_name,
                            log_type="llm_call",
                            model_id=model_id,
                            tool_name=None,
                            message=out_message,
                            message_type=out_type,
                            call_group_id=call_group_id,
                            sequence_num=seq_output,
                            error_text=out_error,
                        ),
                        _loop,
                    )
                else:
                    # Fallback: queue for drain after kickoff
                    _pending_logs.put({
                        "job_id": job_id,
                        "session_id": session_id,
                        "service_name": settings.service_name,
                        "log_type": "llm_call",
                        "model_id": model_id,
                        "tool_name": None,
                        "message": input_message,
                        "message_type": "Input",
                        "call_group_id": call_group_id,
                        "sequence_num": seq_input,
                        "error_text": None,
                    })
                    _pending_logs.put({
                        "job_id": job_id,
                        "session_id": session_id,
                        "service_name": settings.service_name,
                        "log_type": "llm_call",
                        "model_id": model_id,
                        "tool_name": None,
                        "message": out_message,
                        "message_type": out_type,
                        "call_group_id": call_group_id,
                        "sequence_num": seq_output,
                        "error_text": out_error,
                    })
            except Exception:
                logger.exception("wrap_litellm_completion: failed to queue log entry — ignoring")

            # Cycling guard: count consecutive successful LLM calls without a tool success.
            # Raised after logging so the LLM call is still recorded before we abort.
            llm_cycle_limit = getattr(_thread_local, "llm_cycle_limit", None)

            if llm_cycle_limit is not None:

                if exc is None and _response_has_tool_call(result) and result is not None:
                    # ✅ RESET on tool call
                    _thread_local.llm_cycle_count = 0

                else:
                    # ✅ count everything else:
                    # - no tool call
                    # - failures
                    _thread_local.llm_cycle_count = getattr(_thread_local, "llm_cycle_count", 0) + 1

                _count = _thread_local.llm_cycle_count

                if _count > llm_cycle_limit:
                    raise LlmCyclingLimitExceeded(
                        f"{_count} consecutive LLM calls without tool call "
                        f"(limit: {llm_cycle_limit})"
                    )

    _litellm.completion = _record_completion
    try:
        yield
    finally:
        _litellm.completion = _original


def drain_pending_logs() -> list[dict]:
    """Drain and return all pending job log records.

    Call from async context (crew.py) after kickoff() returns.
    """
    logs: list[dict] = []
    while True:
        try:
            logs.append(_pending_logs.get_nowait())
        except queue.Empty:
            break
    return logs
