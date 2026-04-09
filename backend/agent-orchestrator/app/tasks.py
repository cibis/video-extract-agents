from crewai import Task
from crewai import Agent
from typing import Any


def _format_job_history(history: list[dict]) -> str:
    """Produce a clearly bounded, labelled job history block for the planner prompt.

    Designed to be unambiguous for weaker models (e.g. Bedrock Nova Lite):
    each job and each file uses explicit field labels so the model can locate
    the right URL without relying on positional parsing.
    """
    if not history:
        return ""

    total = len(history)
    lines = [
        "\n=== BEGIN JOB HISTORY ===",
        "PURPOSE: These are the jobs that ran before the current one in this session, listed",
        "oldest first. Use this information to understand what work has already been done and",
        "which files are available. If the current prompt references \"the previous job\",",
        "\"the last output\", \"the result from before\", or similar, find the correct file",
        "in the most recent job entry below. Do NOT redo work already done — reuse existing",
        "files by passing their URL directly to the appropriate tool.",
    ]

    for idx, entry in enumerate(history, start=1):
        position = ""
        if total == 1:
            position = " (only previous job)"
        elif idx == 1:
            position = " (oldest)"
        elif idx == total:
            position = " (most recent previous job)"

        lines.append(f"\n--- JOB {idx} OF {total}{position} ---")
        lines.append(f"Prompt: {entry['prompt']}")
        lines.append(f"Status: {entry['status']}")
        if entry.get("error"):
            lines.append(f"Error: {entry['error']}")

        outputs = entry.get("outputs", [])
        if outputs:
            lines.append("\nFinal output video (the deliverable of this job):")
            for o in outputs[:10]:
                lines.append(f"  Filename: {o.get('filename') or 'output'}")
                lines.append(f"  Type: {o.get('content_type') or 'video/mp4'}")
                lines.append(f"  URL: {o['blob_url']}")

        assets = entry.get("assets", [])
        if assets:
            lines.append(
                "\nAnalysis files from this job"
                " (use query_asset with a JSONPath to read specific values —"
                " do NOT load full file content into context):"
            )
            for a in assets[:20]:
                lines.append(f"  Filename: {a.get('filename') or 'asset'}")
                lines.append(f"  Type: {a.get('content_type') or 'application/json'}")
                lines.append(f"  URL: {a['blob_url']}")
                if a.get("description"):
                    lines.append(f"  Description: {a['description']}")

        lines.append(f"--- END JOB {idx} ---")

    lines.append("\n=== END JOB HISTORY ===")
    return "\n".join(lines)


def make_plan_task(
    agent: Agent,
    prompt: str,
    video_urls: list[str],
    video_duration_seconds: float = 0.0,
    tool_catalogue_text: str = "",
    session_assets: list[dict[str, Any]] | None = None,
    job_history: list[dict[str, Any]] | None = None,
    keyframe_index_assets: dict[str, str] | None = None,
) -> Task:
    if video_duration_seconds > 0:
        duration_instruction = (
            f"Primary video duration: {video_duration_seconds:.1f} seconds. "
            f"All timestamps must be clamped to [0, {video_duration_seconds:.1f}]."
        )
    else:
        duration_instruction = "Ensure all timestamps are non-negative."

    video_section = (
        f"Videos ({len(video_urls)}):\n" + "\n".join(f"  - {u}" for u in video_urls)
        if video_urls
        else "No video URLs provided."
    )

    keyframe_section = ""
    if keyframe_index_assets:
        lines = [
            "\nKeyframe index assets — pass these blob paths directly to analysis tools as "
            "'keyframe_index_asset'. Do NOT fetch or expand their contents:"
        ]
        for video_url, blob_path in keyframe_index_assets.items():
            lines.append(f"  - video: {video_url}  →  keyframe_index_asset: {blob_path}")
        keyframe_section = "\n".join(lines)

    session_section = ""
    if session_assets:
        uploaded_files = [a for a in session_assets if a.get("asset_type") == "uploaded_file"]
        uploaded_videos = [a for a in session_assets if a.get("asset_type") == "uploaded_video"]
        other_assets = [
            a for a in session_assets
            if a.get("asset_type") not in ("uploaded_file", "uploaded_video")
        ]

        lines: list[str] = [f"\nExisting session assets ({len(session_assets)} items):"]

        if uploaded_files:
            lines.append(
                "\nUploaded non-video files — call read_asset with blob_url before planning "
                "(for image files, use analyze_scene or detect_objects_vision instead):"
            )
            for a in uploaded_files[:20]:
                ct = a.get("content_type") or "unknown"
                lines.append(
                    f"  - [uploaded_file] blob_url={a.get('blob_url')} "
                    f"filename={a.get('filename')} content_type={ct}"
                )

        if uploaded_videos:
            lines.append(
                "\nUploaded videos (use extract_frames, detect_* tools for analysis):"
            )
            for a in uploaded_videos[:20]:
                lines.append(
                    f"  - [uploaded_video] blob_url={a.get('blob_url')} "
                    f"label={a.get('label')}"
                )

        for a in other_assets[:10]:
            desc_note = f"\n    Description: {a['description']}" if a.get("description") else ""
            lines.append(
                f"  - [{a.get('asset_type')}] blob_url={a.get('blob_url')} "
                f"filename={a.get('filename') or ''} label={a.get('label')}"
                f"{desc_note}"
            )

        session_section = "\n".join(lines)

    history_section = _format_job_history(job_history or [])
    tool_section = f"\n{tool_catalogue_text}" if tool_catalogue_text else ""

    return Task(
        description=(
            f"Analyse this user request and produce a structured extraction plan.\n\n"
            f"User prompt: {prompt}\n\n"
            f"{video_section}\n\n"
            f"{duration_instruction}\n"
            f"{keyframe_section}"
            f"{session_section}"
            f"{history_section}"
            f"{tool_section}\n\n"
            "COST CONSTRAINTS:\n"
            "  - The catalogue above marks every tool with cost_tier=free or cost_tier=frontier.\n"
            "  - Always exhaust free tools (detect_motion, detect_motion_sports, detect_objects, "
            "estimate_height_above_surface, transcribe_audio, query_asset) before proposing "
            "frontier tools.\n"
            "  - Only include a frontier tool (analyze_scene, detect_objects_vision) when free "
            "tools cannot answer the question.\n"
            "  - When including frontier tools, the mandatory pipeline is:\n"
            "      1. Run a free detection tool (detect_motion_sports, detect_objects, etc.) "
            "to get a result_asset.\n"
            "      2. Call write_query_asset on that result_asset "
            "(blob_url=<result_asset>, job_id=<job_id>, video_url=<video_url>). "
            "Use jsonpath='$.frames[?(@.segment_index == 0)]' to get exactly the first "
            "frame of every detected segment (the canonical pattern for semantic vision checks), "
            "'$.frames[?(@.segment_index >= 0)]' for all frames that belong to any segment, "
            "'$.frames[*]' for all scored frames, or a value filter such as "
            "'$.frames[?(@.motion_score > 0.7)]' or '$.frames[?(@.detection_count > 0)]'. "
            "Each segment also carries first_frame_index and last_frame_index pointing into "
            "the frames array for direct index access without timestamp arithmetic.\n"
            "      3. Pass write_query_asset.result_asset as frames_asset to analyze_scene "
            "or detect_objects_vision — never pass the raw detection result_asset directly.\n"
            "  - Never instruct agents to read a full result_asset blob — always use query_asset "
            "with a targeted JSONPath to retrieve only the values needed.\n\n"
            "For each step in the plan, select the most appropriate tool from the catalogue above. "
            "When specifying tools in selected_tools, always include the keyframe_index_asset path "
            "for the relevant video so the analysis agent can pass it to extract_frames and detect_* tools. "
            "When calling any processing tool (extract_clip, extract_clips_bulk, merge_clips, "
            "split_video, transform_video, write_asset), always include both job_id and session_id "
            "in the tool inputs so that all generated artifacts are stored under the correct path.\n"
            "Output a JSON plan with:\n"
            "  - videos: list of video URLs this plan applies to\n"
            "  - segments: list of {start_seconds, end_seconds, reason, video_url}\n"
            "  - selected_tools: list of {tool_name, rationale, "
            "keyframe_index_asset (from above)}\n"
            "  - operations: list of processing steps\n"
            "  - final_output_name: output filename"
        ),
        expected_output=(
            "A JSON object with: videos, segments, selected_tools, operations, final_output_name."
        ),
        agent=agent,
    )


def make_analysis_task(
    agent: Agent,
    video_urls: list[str],
    job_id: str = "",
    session_id: str | None = None,
    keyframe_index_assets: dict[str, str] | None = None,
) -> Task:
    video_list = "\n".join(f"  - {u}" for u in video_urls) if video_urls else "  (none)"

    context_block = (
        f"Job context — include these values in every tool call:\n"
        f"  job_id: {job_id}\n"
        f"  session_id: {session_id or ''}\n"
    )
    if keyframe_index_assets:
        kf_lines = [
            "Keyframe index assets (pass as 'keyframe_index_asset' to extract_frames "
            "and detect_* tools — do NOT expand their contents):"
        ]
        for video_url, blob_path in keyframe_index_assets.items():
            kf_lines.append(f"  - {video_url}  →  {blob_path}")
        context_block += "\n".join(kf_lines) + "\n"

    return Task(
        description=(
            f"{context_block}\n"
            f"Analyse the following videos using the tools selected in the extraction plan:\n"
            f"{video_list}\n\n"
            "COST CONSTRAINTS:\n"
            "  - Free tools (detect_motion, detect_motion_sports, detect_objects, "
            "estimate_height_above_surface, transcribe_audio, query_asset, extract_frames) "
            "have no API cost — use them with the largest frame_batch_size safe for memory "
            "(50–100 for CV tools; 20 per batch for estimate_height_above_surface).\n"
            "  - Frontier tools (analyze_scene, detect_objects_vision) cost per batch. "
            "Only call these when the plan explicitly requires them. "
            "The mandatory pipeline before any frontier tool call is:\n"
            "      1. Run a free detection tool (detect_motion_sports, detect_objects, etc.) "
            "to get a result_asset.\n"
            "      2. Call write_query_asset on that result_asset "
            "(blob_url=<result_asset>, job_id=<job_id>, video_url=<video_url>). "
            "Use jsonpath='$.frames[?(@.segment_index == 0)]' to get exactly the first "
            "frame of every detected segment (the canonical pattern for semantic vision checks), "
            "'$.frames[?(@.segment_index >= 0)]' for all frames that belong to any segment, "
            "'$.frames[*]' for all scored frames, or a value filter such as "
            "'$.frames[?(@.motion_score > 0.7)]' or '$.frames[?(@.detection_count > 0)]'. "
            "Each segment also carries first_frame_index and last_frame_index pointing into "
            "the frames array for direct index access without timestamp arithmetic.\n"
            "      3. Pass write_query_asset.result_asset as frames_asset to analyze_scene "
            "or detect_objects_vision — never pass a raw detection result_asset directly.\n"
            "  - When you need values from a result blob, use query_asset with a specific "
            "JSONPath (e.g. '$.high_motion_segments[*]' or '$.frames[*].timestamp_seconds') "
            "instead of reading the full blob.\n\n"
            "For each tool specified in selected_tools, invoke it with the required inputs. "
            "Always pass job_id and session_id to every tool call. "
            "Pass keyframe_index_asset to extract_frames and detect_* tools. "
            "For estimate_height_above_surface, pass frames_asset=<result_asset from extract_frames> "
            "— it does not accept keyframe_index_asset.\n\n"
            "Frontier vision tools (analyze_scene, detect_objects_vision) use the configured "
            "model automatically — do not pass a model parameter.\n\n"
            "Each detection and motion tool returns a 'summary.segments' list of already-merged "
            "time intervals where the target content was found. Collect the segments from tool "
            "summaries across all tool calls and all videos. Detection tools automatically include "
            "the source 'video_url' in each segment — do NOT strip or omit 'video_url' when "
            "merging segments. Every segment passed to write_segments_asset must retain its "
            "'video_url' field so the processing agent extracts clips from the correct source "
            "video. If a tool's summary.segments is empty, that tool found no matching content.\n\n"
            "Never expand or read 'result_asset' blobs in your reasoning — use only the "
            "summary fields returned by each tool. Use query_asset if you need specific values.\n\n"
            "After all detection and motion tools have been called, merge the segments collected "
            "from all tool summaries into a single deduplicated list ordered by start_seconds. "
            "Each segment passed to write_segments_asset MUST be a time range with "
            "end_seconds strictly greater than start_seconds (minimum 1 second duration). "
            "If a tool returns a single-point timestamp where start_seconds == end_seconds, "
            "expand it to [timestamp − 1.5 s, timestamp + 1.5 s] before including it in the list. "
            "Then call write_segments_asset with the merged list, job_id, and session_id. "
            "This persists the segments to blob storage so the processing agent can use "
            "extract_clips_bulk without receiving the full segment list inline.\n\n"
            "If no segments were found across all tools, do NOT call write_segments_asset — "
            "return segments_asset as null.\n\n"
            "Collect results across all videos and return a unified analysis."
        ),
        expected_output=(
            "A JSON object with:\n"
            "  - 'segments_asset': blob URL returned by write_segments_asset, or null if no "
            "segments were found (REQUIRED field).\n"
            "  - 'segments_count': integer count of merged segments written (0 if none found).\n"
            "  - 'detection_assets': list of {tool_name, result_asset, summary} for each "
            "detection or motion tool called — summary contains only aggregate counts "
            "(total_detections, total_duration_seconds, etc.), NOT the full segments array.\n"
            "  - 'frame_assets': list of {result_asset, summary} for each extract_frames call.\n"
            "  - 'transcription_assets': list of {result_asset, summary} for any "
            "transcribe_audio calls.\n"
            "  - any frontier vision tool outputs (analyze_scene, detect_objects_vision) "
            "inline as before."
        ),
        agent=agent,
    )


def make_processing_task(agent: Agent, job_id: str, user_id: str) -> Task:
    return Task(
        description=(
            f"Process the video(s) using the extraction plan and analysis results. "
            f"Extract each identified segment as a clip (respecting the video_url per segment), "
            f"then merge all clips into a final highlight reel. "
            f"Store the output for job {job_id} (user {user_id}).\n\n"
            "COST CONSTRAINTS:\n"
            "  - All processing tools are free (local FFmpeg, blob writes).\n"
            "  - query_asset is also free — use it with a targeted JSONPath to read specific "
            "values from blobs rather than loading full blob content.\n\n"
            "IMPORTANT — finding segments:\n"
            "  - Check the analysis results for 'segments_asset' (a blob URL). If present and "
            "non-null, use it with extract_clips_bulk. If absent or null, check 'segments_count' — "
            "if zero, no matching content was found.\n"
            "  - If segments_asset is null and segments_count is 0, do NOT extract the full video. "
            "Instead return exactly: "
            '{"output_url": null, "reason": "no_matching_segments", '
            '"message": "No matching content was found in the video."}\n\n'
            "IMPORTANT — extracting clips:\n"
            "  - Preferred path: call extract_clips_bulk with segments_asset (from analysis "
            "results), job_id, and session_id. Do NOT pass a top-level video_url — each segment "
            "in segments_asset already carries its own 'video_url' identifying the source video, "
            "and extract_clips_bulk uses that per-segment value to extract each clip from the "
            "correct video. 'video_url' is REQUIRED on every segment; missing it causes an error "
            "— fix by ensuring write_segments_asset was called with segments that include 'video_url'. "
            "Then pass the returned clip_list_asset directly to merge_clips.\n"
            "  - Fallback path (only when segments_asset is unavailable): call extract_clip once "
            "per segment, passing job_id and session_id each time. After each call pass the "
            "returned clip_list_asset as input to the next extract_clip call. Then pass the "
            "final clip_list_asset to merge_clips.\n"
            "  - Do NOT construct or pass a 'clip_urls' array to merge_clips — use "
            "'clip_list_asset' only.\n\n"
            "  - If you need specific values from a result blob (e.g. segment timestamps), "
            "use query_asset with a JSONPath expression rather than reading the full blob.\n\n"
            "Always pass job_id and session_id to split_video, merge_clips, transform_video, "
            "and write_asset so that all generated artifacts are stored under the correct job path.\n\n"
            "If the plan specifies multiple output assets, use write_asset for non-video outputs. "
            'Return your final output strictly as JSON: {"output_url": "..."}. '
            "If additional non-video outputs were generated, include them: "
            '{"output_url": "...", "additional_outputs": [{"blob_url": "...", "filename": "..."}]}.'
        ),
        expected_output=(
            'A JSON object with "output_url" set to the blob storage URL of the final compiled output video. '
            'Example: {"output_url": "https://...mp4"}. '
            'If additional outputs were produced: {"output_url": "...", "additional_outputs": [...]}. '
            'If no segments were found: {"output_url": null, "reason": "no_matching_segments"}.'
        ),
        agent=agent,
    )
