"""Shared utility functions for the agent orchestrator."""
import json
import re


def convert_function_calls_to_react(text: str) -> str:
    """Convert Claude's <function_calls> XML format to CrewAI ReAct format.

    Claude Haiku 4.5 overrides the ReAct text instructions and outputs tool
    calls as:
      <function_calls>
      [{"tool_name": "...", "arguments": {...}}]
      </function_calls>

    CrewAI's ReAct parser requires:
      Action: tool_name
      Action Input: {...}
    """
    match = re.search(r"<function_calls>\s*(\[.*?\])\s*</function_calls>", text, re.DOTALL)
    if not match:
        return text
    try:
        calls = json.loads(match.group(1))
        if not isinstance(calls, list) or not calls:
            return text
        call = calls[0]
        tool_name = call.get("tool_name", "")
        arguments = call.get("arguments", {})
        react_block = f"Action: {tool_name}\nAction Input: {json.dumps(arguments)}"
        return re.sub(
            r"<function_calls>\s*\[.*?\]\s*</function_calls>",
            react_block,
            text,
            flags=re.DOTALL,
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return text


def strip_code_fences(text: str) -> str:
    """Remove markdown code fences from text without altering anything else.

    Safe to call on full ReAct responses — only removes ``` delimiters, leaving
    the Thought / Action / Action Input structure intact.
    """
    if "```" in text:
        text = re.sub(r"```[a-zA-Z]*\n?", "", text)
        text = text.replace("```", "").strip()
    return text


def convert_json_wrapped_react(text: str) -> str:
    """Convert a JSON-object-wrapped ReAct response to plain-text ReAct format.

    Some weaker models (e.g. Amazon Nova Lite on Bedrock) occasionally wrap
    their Thought/Action/Action Input or Thought/Final Answer response inside a
    JSON object instead of emitting the plain-text format CrewAI expects:

      # What Nova Lite emits (JSON-wrapped):
      {"Thought": "...", "Action": "extract_frames", "Action Input": {...}}

      # What CrewAI expects (plain text):
      Thought: ...
      Action: extract_frames
      Action Input: {...}

    A "Final Answer" variant is also handled:
      {"Thought": "...", "Final Answer": {...}}  →  Thought: ...\\nFinal Answer: {...}

    Safety: only triggered when the *entire* response is a single valid JSON
    object AND the object contains an "Action" or "Final Answer" key.  These
    keys never appear in legitimate agent outputs (plan JSON, analysis-result
    JSON, processing-output JSON), so there is no risk of false positives.
    """
    stripped = text.strip()
    if not stripped.startswith("{"):
        return text
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return text
    if not isinstance(obj, dict):
        return text

    has_action = "Action" in obj
    has_final_answer = "Final Answer" in obj
    if not has_action and not has_final_answer:
        return text

    lines: list[str] = []
    thought = obj.get("Thought", "")
    if thought:
        lines.append(f"Thought: {thought}")

    if has_action:
        action = obj.get("Action", "")
        action_input = obj.get("Action Input", {})
        action_input_str = (
            json.dumps(action_input) if isinstance(action_input, dict) else str(action_input)
        )
        lines.append(f"Action: {action}")
        lines.append(f"Action Input: {action_input_str}")
    else:
        final_answer = obj.get("Final Answer", "")
        final_answer_str = (
            json.dumps(final_answer) if isinstance(final_answer, (dict, list)) else str(final_answer)
        )
        lines.append(f"Final Answer: {final_answer_str}")

    return "\n".join(lines)


def extract_json_string(text: str) -> str:
    """
    Extracts the most likely valid JSON substring from LLM output.
    Returns a CLEAN JSON string (not a dict).
    """

    if not text or not isinstance(text, str):
        raise ValueError("Input must be a non-empty string")

    original_text = text
    text = text.strip()

    # -----------------------------
    # 1. Remove markdown code fences
    # -----------------------------
    if "```" in text:
        text = re.sub(r"```[a-zA-Z]*\n?", "", text)
        text = text.replace("```", "").strip()

    # -----------------------------
    # 2. Try if whole text is JSON
    # -----------------------------
    try:
        obj = json.loads(text)
        return json.dumps(obj)
    except Exception:
        pass

    # -----------------------------
    # 3. Extract JSON candidates
    # -----------------------------
    def extract_blocks(s, open_char, close_char):
        stack = []
        start = None
        for i, ch in enumerate(s):
            if ch == open_char:
                if not stack:
                    start = i
                stack.append(ch)
            elif ch == close_char and stack:
                stack.pop()
                if not stack and start is not None:
                    yield s[start:i+1]

    candidates = list(extract_blocks(text, "{", "}"))
    candidates += list(extract_blocks(text, "[", "]"))

    # prioritize longest (most complete)
    candidates = sorted(candidates, key=len, reverse=True)

    # -----------------------------
    # 4. Try parsing candidates
    # -----------------------------
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
            return json.dumps(obj)
        except Exception:
            pass

    # -----------------------------
    # 5. Cleanup + retry
    # -----------------------------
    def clean(candidate: str) -> str:
        # remove trailing commas
        candidate = re.sub(r",\s*([\]}])", r"\1", candidate)

        # fix single quotes (only if no double quotes)
        if candidate.count('"') == 0:
            candidate = candidate.replace("'", '"')

        # remove control chars
        candidate = re.sub(r"[\x00-\x1F\x7F]", "", candidate)

        return candidate

    for candidate in candidates:
        try:
            cleaned = clean(candidate)
            obj = json.loads(cleaned)
            return json.dumps(obj)
        except Exception:
            continue

    # -----------------------------
    # 6. Fallback (simple key-value)
    # -----------------------------
    kv_match = re.findall(r'"?(\w+)"?\s*:\s*"([^"]+)"', text)
    if kv_match:
        obj = {k: v for k, v in kv_match}
        return json.dumps(obj)

    # -----------------------------
    # FAIL
    # -----------------------------
    raise ValueError(f"No valid JSON found in:\n{original_text}")
