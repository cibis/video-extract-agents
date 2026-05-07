from typing import Any
from crewai import Agent, LLM
from crewai.utilities import I18N
from app.config import settings

# Providers with documented seed support (OpenAI and Azure OpenAI).
# Anthropic, AWS Bedrock, and most other providers reject or ignore the
# seed parameter — omit it for those to avoid API errors.
_SEED_SUPPORTED_PREFIXES = ("openai/", "azure/", "gpt-", "o1", "o3", "ft:gpt-")
_FIXED_SEED = 42


def _seed_kwargs(model: str) -> dict[str, int]:
    """Return {'seed': _FIXED_SEED} only for models that support it."""
    if any(model.lower().startswith(p) for p in _SEED_SUPPORTED_PREFIXES):
        return {"seed": _FIXED_SEED}
    return {}


def _make_i18n() -> I18N:
    """Return a custom I18N instance with a clearer tool-repetition guard message.

    CrewAI's default 'task_repeated_usage' message is written in first person
    ("I tried reusing the same input…") which weaker models misread as their own
    prior statement and therefore retry the identical call, creating an infinite
    loop.  The replacement message is third-person and commands the model to emit
    a Final Answer, which works correctly for all models.
    """
    i18n = I18N()
    i18n._prompts["errors"]["task_repeated_usage"] = (
        "SYSTEM: This tool call was blocked. The immediately preceding tool call "
        "was identical and already executed successfully. "
        "Do NOT call this tool again with these parameters. "
        "You MUST output your Final Answer now.\n\n"
    )
    return i18n


_I18N = _make_i18n()


# def make_planner_agent(model: str, rpm_limit: int | None = None) -> Agent:
#     llm = LLM(model=model, temperature=0)
#     return Agent(
#         role="Video Extraction Planner",
#         goal=(
#             "Interpret the user's natural language prompt and generate a precise, "
#             "structured extraction plan describing which video segments to extract, "
#             "with timestamps and operations."
#         ),
#         backstory=(
#             "You are an expert video editor who understands sports, action sequences, "
#             "and creative video editing. You translate user intent into actionable plans.\n\n"
#             "COST DISCIPLINE: The tool catalogue labels every tool with cost_tier=free or "
#             "cost_tier=frontier. Frontier tools make paid API calls; free tools run locally. "
#             "Always plan to exhaust free tools before proposing frontier tools. "
#             "Only include a frontier tool in the plan when free tools cannot answer the question. "
#             "When you do include frontier tools, restrict them to a representative sample of frames "
#             "rather than the full frame set — instruct the analysis agent to use the largest "
#             "frame_batch_size that fits the model's context window.\n\n"
#             "CONTEXT WINDOW DISCIPLINE: Your plan must not include instructions to load full "
#             "result_asset blobs into the agent context. Always instruct agents to use query_asset "
#             "with a targeted JSONPath expression to extract the specific values they need from "
#             "large result blobs. Never read a full blob when a JSONPath query suffices."
#         ),
#         llm=llm,
#         i18n=_I18N,
#         max_rpm=rpm_limit,
#         verbose=(settings.log_level.upper() == "DEBUG"),
#         allow_delegation=False,
#     )

def make_planner_agent(model: str, rpm_limit: int | None = None, max_tokens: int | None = None) -> Agent:
    _max_tokens_kwargs = {"max_tokens": max_tokens} if max_tokens is not None else {}
    llm = LLM(model=model, temperature=0, **_max_tokens_kwargs, **_seed_kwargs(model))
    return Agent(
        role="Video Extraction Planner",
        goal=(
            "Interpret the user's natural language prompt and generate a precise, "
            "structured extraction plan describing which video segments to extract, "
            "with timestamps and operations. The plan must be robust to partial or empty "
            "detection results and executable by weaker models without failure."
        ),
        backstory=(
            "You are an expert video editor who understands sports, action sequences, "
            "and creative video editing. You translate user intent into actionable, "
            "fault-tolerant plans.\n\n"

            "COST DISCIPLINE: The tool catalogue labels every tool with cost_tier=free or "
            "cost_tier=frontier. Frontier tools make paid API calls; free tools run locally. "
            "Always plan to exhaust free tools before proposing frontier tools. "
            "Only include a frontier tool in the plan when free tools cannot answer the question. "
            "When you do include frontier tools, restrict them to a representative sample of frames "
            "rather than the full frame set. Frontier tool batch sizes are determined "
            "automatically by the tool — do not pass frame_batch_size to them.\n\n"

            "CONTEXT WINDOW DISCIPLINE: Your plan must not include instructions to load full "
            "result_asset blobs into the agent context. Always instruct agents to use query_asset "
            "with a targeted JSONPath expression to extract the specific values they need from "
            "large result blobs. Never read a full blob when a JSONPath query suffices.\n\n"

            "ROBUSTNESS REQUIREMENT (CRITICAL):\n"
            "- Detection steps may return zero results. NEVER assume outputs are non-empty.\n"
            "- Plans MUST remain valid and executable even if some detections return nothing.\n"
            "- Treat each detection condition as an independent branch.\n"
            "- Do NOT create step dependencies where one detection failing breaks the pipeline.\n\n"

            "LOGICAL INTERPRETATION RULES:\n"
            "- 'A and B':\n"
            "    segments_A = detect A\n"
            "    segments_B = detect B\n"
            "    final_segments = intersection(segments_A, segments_B)\n"
            "    IF intersection is empty → fallback to union(segments_A, segments_B)\n\n"
            "- 'A or B', 'A and/or B', 'any':\n"
            "    final_segments = union(segments_A, segments_B)\n\n"
            "- 'any <object>': treat as an independent OR condition\n\n"
            "- NEVER return empty results solely because one branch failed.\n\n"

            "GRACEFUL DEGRADATION:\n"
            "- Prefer partial results over empty outputs.\n"
            "- If some conditions fail, return segments from successful detections.\n\n"

            "EXECUTION SAFETY CONTRACT:\n"
            "- Plans are executed by weaker models.\n"
            "- They cannot infer missing steps or recover from errors.\n"
            "- ALL logic must be explicit.\n"
            "- ALWAYS define how results are combined (union, intersection, fallback).\n"
            "- NEVER rely on implicit behavior.\n\n"

            "PLANNING REQUIREMENTS:\n"
            "- Always create separate detection steps per condition.\n"
            "- Always combine results explicitly using union/intersection.\n"
            "- Always ensure downstream steps work with empty or partial inputs.\n"
            "- Avoid chaining detections where one depends on another's success.\n"
        ),
        llm=llm,
        i18n=_I18N,
        max_rpm=rpm_limit,
        verbose=(settings.log_level.upper() == "DEBUG"),
        allow_delegation=False,
    )


def make_analysis_agent(model: str, tools: list[Any] | None = None, rpm_limit: int | None = None) -> Agent:
    llm = LLM(model=model, temperature=0, **_seed_kwargs(model))
    return Agent(
        role="Video Analysis Agent",
        goal=(
            "Use available analysis tools to examine keyframes, detect motion, and identify objects. "
            "Always pass job_id, session_id, and keyframe_index_asset to every tool call. "
            "Tool results are stored as blob assets — use only the 'summary' fields in your reasoning; "
            "never expand 'result_asset' paths. "
            "When you need specific values from a large result blob, call query_asset with a "
            "targeted JSONPath expression instead of reading the full blob."
        ),
        backstory=(
            "You are a computer vision expert specialising in video analysis. "
            "You use tools to analyse frames and motion to locate relevant content.\n\n"
            "COST DISCIPLINE: Free tools (detect_motion, detect_motion_sports, detect_objects, "
            "estimate_height_above_surface, transcribe_audio, query_asset, extract_frames, "
            "write_asset, patch_asset, normalize_segments) "
            "have no API cost — use them freely and with the largest frame_batch_size that is "
            "safe for memory. "
            "Frontier tools (analyze_scene, detect_objects_vision) incur an API call per batch — "
            "only call these when free tools are insufficient. Do not pass frame_batch_size to "
            "frontier vision tools — batch sizes are determined automatically by the tool.\n\n"
            "BATCH EFFICIENCY: For free CV tools (OpenCV, YOLO) use 50–100 frames per batch "
            "or the full frame count for short clips. For estimate_height_above_surface use "
            "20 frames per batch (default) — it runs a neural depth model per frame.\n\n"
            "MANDATORY PIPELINE — extract_frames FIRST:\n"
            "For every video, call extract_frames(keyframe_index_asset=<path>, ...) as the FIRST "
            "tool call before any other analysis tool. extract_frames returns result_asset — a blob "
            "containing the decoded frame images. Pass this result_asset as frames_asset to:\n"
            "  - detect_motion\n"
            "  - detect_motion_sports\n"
            "  - detect_objects\n"
            "  - analyze_scene\n"
            "  - estimate_height_above_surface\n"
            "keyframe_index_asset is ONLY accepted by extract_frames. Never pass it to any other tool. "
            "Never skip extract_frames even if the plan appears to allow it.\n\n"
            "SAMPLING STRATEGY: For frontier tools, first run a free tool on all frames to identify "
            "candidate segments, then apply the frontier tool only to those candidate frames. "
            "Do not run a frontier tool on the entire frame set.\n\n"
            "ANALYZE_SCENE WITH QUESTION: When analyze_scene is called with a question parameter, "
            "each non-error frame in the result_asset includes a 'matched' boolean field "
            "(true = frame answers the question positively). The summary also includes 'matched_count'. "
            "Use query_asset with '$.frames[?(@.matched == true)]' to retrieve only matching frames "
            "and pass their timestamps to the processing agent for clip extraction. "
            "Do not rely on description text — use matched for all boolean filtering.\n\n"
            "CONTEXT WINDOW DISCIPLINE: Never load full result_asset blob content into your context. "
            "Use query_asset with a specific JSONPath (e.g. '$.segments[*]' or "
            "'$.frames[?(@.detection_count > 0)].timestamp_seconds') to retrieve only the values you need."
        ),
        llm=llm,
        i18n=_I18N,
        max_iter=20,
        tools=tools or [],
        max_rpm=rpm_limit,
        verbose=(settings.log_level.upper() == "DEBUG"),
        allow_delegation=False,
    )


def make_processing_agent(model: str, tools: list[Any] | None = None, rpm_limit: int | None = None) -> Agent:
    llm = LLM(model=model, temperature=0, **_seed_kwargs(model))
    return Agent(
        role="Video Processing Agent",
        goal=(
            "Execute video processing operations — extract clips and merge them into the final output. "
            "Prefer extract_clips_bulk when segments_asset is available from the analysis results — "
            "call it once with segments_asset (and video_url, job_id, session_id) to extract all clips, "
            "then pass the returned clip_list_asset directly to merge_clips. "
            "Fall back to chained extract_clip calls only when no segments_asset is available. "
            "Always pass job_id and session_id to every tool call — "
            "extract_clips_bulk, extract_clip, split_video, merge_clips, transform_video, "
            "write_asset, patch_asset, and normalize_segments."
        ),
        backstory=(
            "You are a video processing specialist. You use FFmpeg-based tools "
            "to cut, merge, and transform video segments into a polished highlight reel.\n\n"
            "COST DISCIPLINE: All processing tools are free (local FFmpeg). "
            "query_asset, write_asset, patch_asset, and normalize_segments are also free — use them freely. "
            "Use patch_asset to update fields in an existing JSON blob in-place; "
            "use normalize_segments to expand short segments and merge overlaps before extraction; "
            "use query_asset with a targeted JSONPath to read specific values "
            "from analysis result blobs rather than loading the full blob.\n\n"
            "CONTEXT WINDOW DISCIPLINE: Never load full result_asset blob content into your context. "
            "If you need to inspect a blob (e.g. to retrieve segment timestamps or clip URLs), "
            "use query_asset with a specific JSONPath expression to retrieve only what you need."
        ),
        llm=llm,
        i18n=_I18N,
        max_iter=20,
        tools=tools or [],
        max_rpm=rpm_limit,
        verbose=(settings.log_level.upper() == "DEBUG"),
        allow_delegation=False,
    )
