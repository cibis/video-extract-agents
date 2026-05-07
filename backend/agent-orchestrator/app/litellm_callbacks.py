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


class LlmCallLimitExceeded(Exception):
    """Raised when a model exceeds max_calls_per_job for the current job."""


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
        # Hard stop: raise from ToolUsage.use() context, not from litellm.completion.
        # Exceptions from ToolUsage.use() propagate through crew.kickoff();
        # exceptions from litellm.completion are caught internally by CrewAI.
        _exceeded = getattr(_thread_local, "call_limit_exceeded_msg", None)
        if _exceeded:
            raise LlmCallLimitExceeded(_exceeded)

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

def _compress_messages(messages: list, model: str, original_fn) -> tuple[list, str]:
    """Compress the full conversation history using the planner model.

    Scans the first few messages to locate the task message (the one that contains
    the "operations" JSON array) and keeps everything up to and including it unchanged.
    Only the action/observation turns that follow are compressed.

    Handles multiple layout variants:
      - [0] system, [1] task, [2..N] turns          (standard)
      - [0] placeholder, [1] system, [2] task, [3..N] turns  (CrewAI)
      - no task message found → fall back to keeping first 2 messages

    Returns (new_messages, summary_text). Returns (messages, "") if nothing to compress.
    """
    import json as _json, re as _re2

    # Scan the first few messages to find the one containing the operations plan.
    _SCAN_LIMIT = 5
    _task_msg_idx = None
    for _i, _m in enumerate(messages[:_SCAN_LIMIT]):
        _c = _m.get("content", "")
        if isinstance(_c, str) and '"operations"' in _c:
            _task_msg_idx = _i
            break

    # keep_head = everything up to and including the task message (or first 2 as fallback)
    _keep_head_end = (_task_msg_idx + 1) if _task_msg_idx is not None else 2

    if len(messages) <= _keep_head_end:
        return messages, ""

    keep_head = messages[:_keep_head_end]
    to_compress = messages[_keep_head_end:]

    formatted = "\n".join(
        f"[{m.get('role', '?').upper()}]: {m.get('content', '')}"
        for m in to_compress
        if isinstance(m.get("content"), str)
    )

    # Extract plan step list from the task message so the compressor can reference
    # step numbers in REMAINING WORK instead of re-describing steps in prose.
    _ops_context = ""
    if _task_msg_idx is not None:
        try:
            task_content = messages[_task_msg_idx].get("content", "")
            _ops_match = _re2.search(r'"operations"\s*:\s*\[', task_content)
            if _ops_match:
                obj_start = task_content.rfind('{', 0, _ops_match.start())
                if obj_start != -1:
                    data = _json.loads(task_content[obj_start:])
                    ops = data.get("operations", [])
                    if ops:
                        lines = [
                            f"  {op['step']}. {op['operation']}"
                            for op in ops
                            if "step" in op and "operation" in op
                        ]
                        _ops_context = (
                            "Plan steps (already in agent context above — use these step numbers in REMAINING WORK):\n"
                            + "\n".join(lines) + "\n\n"
                        )
        except Exception:
            pass  # degrade gracefully; REMAINING WORK falls back to prose

    compression_prompt = (
        "You are compressing an AI agent's conversation history to free context window space.\n"
        "The agent has access to tools defined in its system prompt. Your summary must allow it to continue using those tools.\n\n"
        "CRITICAL RULES — violating any of these will break the agent:\n"
        "1. NEVER reproduce the following ReAct keywords in your output: "
        "'Final Answer', 'I now know the final answer', 'Thought:', 'Action:', 'Action Input:', 'Observation:'.\n"
        "   If the history contains 'Final Answer' or 'I now know the final answer' text, "
        "this means the agent attempted to end the task prematurely but FAILED — the task is still running.\n"
        "   Treat it as a completed tool step, NOT as the current outcome.\n"
        "2. REMAINING WORK must always contain at least one item. "
        "If the task were truly complete, this compression would not be happening.\n"
        "3. Preserve all blob/asset URLs character-for-character — never paraphrase or shorten a URL.\n\n"
        + _ops_context
        + "Write the summary using EXACTLY these three sections:\n\n"
        "COMPLETED STEPS:\n"
        "List every tool call made and its result. For each: tool name, key outcome, and result asset URL if any.\n\n"
        "KEY DATA:\n"
        "All numerical findings: counts, heights, durations, timestamps, segment boundaries, scores.\n\n"
        "REMAINING WORK:\n"
        "State the next step using EXACTLY this format: 'Continue from step #N'\n"
        "If there is partial progress within that step (e.g., 3 of 9 videos processed), add ONE short line stating only what remains for that step — do not re-describe the step itself.\n"
        "If plan steps are not available above, list the exact tools still to call. Must not be empty.\n\n"
        "Strip completely: verbose reasoning, repeated observations, raw frame listings, intermediate deliberation, "
        "and any ReAct-format keywords listed in rule 1 above.\n\n"
        f"Conversation history to compress:\n{formatted}"
    )
    planner = getattr(_thread_local, "recovery_model", None) or model
    resp = original_fn(
        model=planner,
        messages=[{"role": "user", "content": compression_prompt}],
        temperature=0,
    )
    summary = resp.choices[0].message.content or ""
    # Scrub any hallucinated "Final Answer" / "I now know the final answer" lines
    # that Haiku may reproduce verbatim from the history despite the instruction above.
    # Nova interprets these phrases as a terminal state and restarts the task from scratch.
    import re as _re
    _forbidden = _re.compile(
        r'^[^\n]*(?:Final\s+Answer|I\s+now\s+know\s+the\s+final\s+answer)[^\n]*$',
        _re.IGNORECASE | _re.MULTILINE,
    )
    summary = _forbidden.sub('', summary)
    summary = _re.sub(r'\n{3,}', '\n\n', summary).strip()
    compressed_msg = {
        "role": "user",
        "content": (
            f"[CONTEXT COMPRESSED — {len(to_compress)} messages replaced with task state summary]\n"
            "Your full tool catalogue remains in the system prompt above. "
            "The original task and operations plan (with step numbers) remain in the message above this one. "
            "Continue the task using the summary below.\n\n"
            + summary
        ),
    }
    return keep_head + [compressed_msg], summary


def _log_compression(
    original_tokens: int,
    threshold_tokens: int,
    messages_before: list,
    messages_after: list,
    model: str,
) -> None:
    """Write a paired context_compression log entry to job_logs (Input + Output).

    Input message  = full messages array sent to the agent before compression.
    Output message = full messages array the agent will receive after compression.
    Both are stored as JSON so the session history renders them identically to
    llm_call entries, making the before/after context directly inspectable.
    """
    job_id = getattr(_thread_local, "job_id", None)
    if not job_id:
        return
    session_id = getattr(_thread_local, "session_id", None)
    counter = getattr(_thread_local, "seq_counter", None)
    planner = getattr(_thread_local, "recovery_model", None) or model
    call_group_id = str(uuid.uuid4())

    try:
        input_msg = json.dumps(messages_before, ensure_ascii=False)
    except Exception:
        input_msg = str(messages_before)
    try:
        output_msg = json.dumps(messages_after, ensure_ascii=False)
    except Exception:
        output_msg = str(messages_after)

    seq_input = next(counter) if counter is not None else 0
    seq_output = next(counter) if counter is not None else 0

    _loop = getattr(_thread_local, "event_loop", None)

    def _queue_or_schedule(entry: dict) -> None:
        if _loop is not None and _loop.is_running():
            from app.db import record_job_log
            asyncio.run_coroutine_threadsafe(record_job_log(**entry), _loop)
        else:
            _pending_logs.put(entry)

    _queue_or_schedule({
        "job_id": job_id,
        "session_id": session_id,
        "service_name": settings.service_name,
        "log_type": "context_compression",
        "model_id": planner,
        "tool_name": None,
        "message": input_msg,
        "message_type": "Input",
        "call_group_id": call_group_id,
        "sequence_num": seq_input,
        "error_text": None,
    })
    _queue_or_schedule({
        "job_id": job_id,
        "session_id": session_id,
        "service_name": settings.service_name,
        "log_type": "context_compression",
        "model_id": planner,
        "tool_name": None,
        "message": output_msg,
        "message_type": "Output",
        "call_group_id": call_group_id,
        "sequence_num": seq_output,
        "error_text": None,
    })


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
        # Bail immediately if the call limit was exceeded on a prior call.
        # Raising here prevents unnecessary API calls, but CrewAI's LLM wrapper
        # still catches it. The authoritative abort raise is in _guarded_use.
        _exceeded_msg = getattr(_thread_local, "call_limit_exceeded_msg", None)
        if _exceeded_msg:
            raise LlmCallLimitExceeded(_exceeded_msg)

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
        # Capture CrewAI's raw message count before any modifications — used below to
        # identify which messages are "new" since the last compression round.
        n_crewai_len = len(messages) if isinstance(messages, list) else 0
        if isinstance(messages, list) and messages and messages[-1].get("role") == "assistant":
            logger.debug("_record_completion: messages end with assistant — appending user turn")
            kwargs["messages"] = messages + [{"role": "user", "content": "Continue."}]

        # Re-inject compressed state: CrewAI rebuilds its full message history from
        # internal state on every call, so the kwargs["messages"] = new_messages
        # assignment inside the compression block below only affects the current call.
        # On the next call CrewAI sends the original uncompressed history again,
        # triggering re-compression in a loop.
        # Fix: after each compression we store the compressed head in _thread_local
        # and inject it here, keeping only the tail messages that are genuinely new
        # since the last compression.
        compressed_head = getattr(_thread_local, "compressed_head", None)
        n_crewai_at_compression = getattr(_thread_local, "n_crewai_at_compression", 0)
        if (
            compressed_head is not None
            and isinstance(kwargs.get("messages"), list)
            and n_crewai_len >= n_crewai_at_compression
        ):
            current = kwargs["messages"]
            new_tail = current[n_crewai_at_compression:]
            kwargs["messages"] = compressed_head + new_tail
            logger.debug(
                "_record_completion: injected compressed head (%d msgs) + %d new tail msgs",
                len(compressed_head), len(new_tail),
            )

        # Context window compression: if the messages list exceeds the per-model
        # threshold, compress the full conversation history using the planner model
        # before making the real call. Uses _original directly to avoid recursion.
        context_windows = getattr(_thread_local, "context_windows", {})
        messages = kwargs.get("messages")
        model_for_cw = kwargs.get("model") or (args[0] if args else None)
        if (
            isinstance(messages, list)
            and len(messages) > 1
            and model_for_cw in context_windows
        ):
            import litellm as _lm
            token_count = _lm.token_counter(model=model_for_cw, messages=messages)
            cw_info = context_windows[model_for_cw]
            per_model_threshold = cw_info.get("compression_threshold", settings.context_compression_threshold)
            threshold_tokens = int(cw_info["context_window_tokens"] * per_model_threshold)
            if token_count > threshold_tokens:
                logger.info(
                    "_record_completion: context %d tokens > threshold %d — compressing",
                    token_count, threshold_tokens,
                )
                new_messages, summary = _compress_messages(messages, model_for_cw, _original)
                if summary:
                    _log_compression(token_count, threshold_tokens, messages, new_messages, model_for_cw)
                    kwargs["messages"] = new_messages
                    # Persist so the next call re-uses the compressed head instead of
                    # seeing CrewAI's full uncompressed history (which would re-trigger).
                    _thread_local.compressed_head = new_messages
                    _thread_local.n_crewai_at_compression = n_crewai_len

        result = None
        exc = None
        _raw_content: str | None = None
        try:
            result = _original(*args, **kwargs)
            if result and getattr(result, "choices", None):
                _raw_content = result.choices[0].message.content
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
                    if result and getattr(result, "choices", None):
                        _raw_content = result.choices[0].message.content
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
                    out_message = _raw_content or ""
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

            # Hard stop: per-model call count vs max_calls_per_job (read from DB via context_windows).
            # Runs after logging so the triggering call is always recorded first.
            _cw = getattr(_thread_local, "context_windows", {})
            _call_model = kwargs.get("model", args[0] if args else None)
            if _call_model and _call_model in _cw:
                _max_calls = _cw[_call_model].get("max_calls_per_job")
                if _max_calls:
                    _counts = getattr(_thread_local, "llm_call_counts", {})
                    _counts[_call_model] = _counts.get(_call_model, 0) + 1
                    _thread_local.llm_call_counts = _counts
                    if _counts[_call_model] >= _max_calls:
                        _limit_msg = (
                            f"Job terminated: model '{_call_model}' has made "
                            f"{_counts[_call_model]} LLM calls in this job, reaching the "
                            f"hard stop limit of {_max_calls} calls per job. "
                            "The job was aborted to prevent runaway execution. "
                            "Review the session history for repeated compression or tool-retry loops."
                        )
                        logger.error("_record_completion: %s", _limit_msg)
                        _job_id2 = getattr(_thread_local, "job_id", None)
                        _session_id2 = getattr(_thread_local, "session_id", None)
                        _counter2 = getattr(_thread_local, "seq_counter", None)
                        _loop2 = getattr(_thread_local, "event_loop", None)
                        if _job_id2:
                            _limit_entry = {
                                "job_id": _job_id2,
                                "session_id": _session_id2,
                                "service_name": settings.service_name,
                                "log_type": "error",
                                "model_id": _call_model,
                                "tool_name": None,
                                "message": _limit_msg,
                                "message_type": "Error",
                                "call_group_id": str(uuid.uuid4()),
                                "sequence_num": next(_counter2) if _counter2 else 0,
                                "error_text": _limit_msg,
                            }
                            if _loop2 is not None and _loop2.is_running():
                                from app.db import record_job_log
                                asyncio.run_coroutine_threadsafe(record_job_log(**_limit_entry), _loop2)
                            else:
                                _pending_logs.put(_limit_entry)
                        # Store the flag — raising here is caught by CrewAI's LLM wrapper.
                        # The actual abort raise happens in _guarded_use (ToolUsage.use context)
                        # which propagates outside CrewAI's internal retry handler.
                        _thread_local.call_limit_exceeded_msg = _limit_msg

    # Reset per-job compression state and call counters so a job running on a reused
    # thread does not inherit stale state from the previous job on this thread.
    _thread_local.compressed_head = None
    _thread_local.n_crewai_at_compression = 0
    _thread_local.llm_call_counts = {}  # model → call count this job
    _thread_local.call_limit_exceeded_msg = None

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
