"""MCP tool payload validation — schema-first, constructive error messages."""
from __future__ import annotations

import jsonschema
from jsonschema import Draft7Validator


def _fmt_path(path) -> str:
    """Convert a jsonschema error deque path to a human-readable field reference."""
    if not path:
        return "payload"
    parts = []
    for p in path:
        if isinstance(p, int):
            parts.append(f"[{p}]")
        else:
            parts.append(f"'{p}'" if parts else f"'{p}'")
    # Collapse array index notation cleanly: 'segments'[0] → 'segments[0]'
    result = parts[0]
    for p in parts[1:]:
        if p.startswith("["):
            result += p
        else:
            result += f".{p}"
    return result


def _humanise(error: jsonschema.ValidationError) -> str:
    """Turn a jsonschema ValidationError into a single actionable sentence."""
    path = _fmt_path(list(error.absolute_path))

    if error.validator == "required":
        missing = error.validator_value[0] if isinstance(error.validator_value, list) else error.validator_value
        # jsonschema reports missing as "X is a required property"
        # Extract the property name from the message directly when path is payload root
        prop = error.message.split("'")[1] if "'" in error.message else missing
        return f"'{prop}' is required but was not provided."

    if error.validator == "type":
        expected = error.validator_value
        got = type(error.instance).__name__
        return f"{path}: expected {expected}, got {got} ({error.instance!r})."

    if error.validator == "minLength":
        return f"{path}: must be a non-empty string (got empty string)."

    if error.validator == "minimum":
        return f"{path}: must be >= {error.validator_value}, got {error.instance}."

    if error.validator == "maximum":
        return f"{path}: must be <= {error.validator_value}, got {error.instance}."

    if error.validator == "minItems":
        return f"{path}: must contain at least {error.validator_value} item(s), got {len(error.instance)}."

    if error.validator == "enum":
        allowed = ", ".join(repr(v) for v in error.validator_value)
        return f"{path}: must be one of [{allowed}], got {error.instance!r}."

    # Generic fallback
    return f"{path}: {error.message}"


def _cross_field_errors(tool_name: str, payload: dict) -> list[str]:
    """Validate constraints that cannot be expressed in JSON Schema."""
    errors: list[str] = []

    # Segment time-range validation: end_seconds must be > start_seconds
    if tool_name in ("write_segments_asset", "extract_clips_bulk"):
        segments = payload.get("segments") or []
        for i, seg in enumerate(segments):
            if not isinstance(seg, dict):
                continue
            start = seg.get("start_seconds")
            end = seg.get("end_seconds")
            if start is not None and end is not None:
                try:
                    if float(end) <= float(start):
                        errors.append(
                            f"'segments[{i}].end_seconds' must be strictly greater than "
                            f"'start_seconds' (got start={start}, end={end}). "
                            "Expand the segment to cover at least 1 second."
                        )
                except (TypeError, ValueError):
                    pass  # type errors already caught by schema validator

    # merge_clips: must supply clip_list_asset or a non-empty clip_urls
    if tool_name == "merge_clips":
        has_asset = bool(payload.get("clip_list_asset", "").strip() if isinstance(payload.get("clip_list_asset"), str) else "")
        has_urls = bool(payload.get("clip_urls"))
        if not has_asset and not has_urls:
            errors.append(
                "Either 'clip_list_asset' (blob URL from extract_clip/extract_clips_bulk) "
                "or a non-empty 'clip_urls' list must be provided."
            )

    # extract_clips_bulk: must supply segments_asset or a non-empty segments list
    if tool_name == "extract_clips_bulk":
        has_asset = bool(payload.get("segments_asset", "").strip() if isinstance(payload.get("segments_asset"), str) else "")
        has_segs = bool(payload.get("segments"))
        if not has_asset and not has_segs:
            errors.append(
                "Either 'segments_asset' (blob URL from write_segments_asset) "
                "or a non-empty inline 'segments' array must be provided."
            )

    return errors


def validate_tool_payload(
    tool_name: str,
    schema: dict,
    payload: dict,
) -> list[str]:
    """
    Validate *payload* against *schema* for the named tool.

    Returns a list of human-readable error strings.
    An empty list means the payload is valid.
    """
    validator = Draft7Validator(schema)
    errors = [_humanise(e) for e in sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path))]
    errors += _cross_field_errors(tool_name, payload)
    return errors
