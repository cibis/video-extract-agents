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
import json
import logging
import queue
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Generator

from app.config import settings
from app.utils import strip_code_fences, convert_function_calls_to_react

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
        obs_idx = tool_input.find("\nObservation:")
        if obs_idx != -1:
            tool_input = tool_input[:obs_idx]
        return _orig(tool_input)

    _parser._safe_repair_json = _patched
    logger.debug("_patch_crewai_parser: _safe_repair_json patched to strip \\nObservation: suffix")


_patch_crewai_parser()


class ToolRetryLimitExceeded(Exception):
    """Raised when a tool exceeds tool_max_retry_limit consecutive ToolUsageErrors."""


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
            logger.warning(
                "guard_tool_usage_errors: tool '%s' failure %d/%d — %s",
                tool_name, _counts[tool_name], limit, str(result)[:200],
            )
            if _counts[tool_name] >= limit:
                raise ToolRetryLimitExceeded(
                    f"Tool '{tool_name}' failed {_counts[tool_name]} consecutive "
                    f"times (limit: {limit}). Last error: {result}"
                )
        else:
            if tool_name in _counts:
                logger.debug("guard_tool_usage_errors: tool '%s' succeeded — resetting counter", tool_name)
            _counts.pop(tool_name, None)  # reset on successful call

        return result

    ToolUsage.use = _guarded_use
    try:
        yield
    finally:
        ToolUsage.use = _orig_use


def _strip_response_fences(result) -> None:
    """Normalise LLM response content to CrewAI ReAct format in-place.

    Two transformations are applied before CrewAI's parser sees the content:

    1. Claude Haiku 4.5 overrides the ReAct text instructions and emits tool
       calls as <function_calls>[{"tool_name":...,"arguments":...}]
       </function_calls> XML.  convert_function_calls_to_react() rewrites this
       to the expected Action: / Action Input: format.

    2. Weaker models (e.g. Amazon Nova Lite) wrap their ReAct output in
       ```...``` code blocks.  strip_code_fences() removes the delimiters so
       ast.literal_eval() can parse the Action Input JSON cleanly.
    """
    if result is not None and getattr(result, "choices", None):
        content = result.choices[0].message.content or ""
        if "<function_calls>" in content:
            content = convert_function_calls_to_react(content)
        if "```" in content:
            content = strip_code_fences(content)
        result.choices[0].message.content = content


# Thread-safe queue for job log data collected during crew.kickoff()
_pending_logs: queue.Queue = queue.Queue()

# Per-thread job context set inside the executor thread before kickoff
_thread_local = threading.local()


def set_job_context(job_id: str, session_id: str | None) -> None:
    """Call from the executor thread (inside the kickoff lambda) before kickoff."""
    _thread_local.job_id = job_id
    _thread_local.session_id = session_id


def clear_job_context() -> None:
    """Call from the executor thread after kickoff completes."""
    _thread_local.job_id = None
    _thread_local.session_id = None


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


@contextmanager
def wrap_litellm_completion() -> Generator[None, None, None]:
    """Context manager that monkey-patches litellm.completion for the duration.

    Records every call (success and failure) to _pending_logs.
    Must be entered from the executor thread that runs crew.kickoff().
    """
    import litellm as _litellm

    _original = _litellm.completion

    def _record_completion(*args, **kwargs):
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

            # Transient InternalServerError (e.g. Docker DNS blip resolving
            # api.anthropic.com): retry up to 3 times with 5 s sleep.
            # litellm.num_retries only covers ServiceUnavailableError /
            # APIConnectionError; InternalServerError is a separate class.
            import litellm as _litellm_inner
            if isinstance(e, _litellm_inner.InternalServerError):
                for attempt in range(1, 4):
                    logger.warning(
                        "_record_completion: InternalServerError (attempt %d/3) — "
                        "sleeping 5 s before retry: %s",
                        attempt, e,
                    )
                    time.sleep(5)
                    try:
                        result = _original(*args, **kwargs)
                        _strip_response_fences(result)
                        return result
                    except _litellm_inner.InternalServerError as e_retry:
                        if attempt == 3:
                            exc = e_retry
                            raise
                    except Exception as e_retry:
                        exc = e_retry
                        raise

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
