"""CrewAI crew definition and kickoff."""
import asyncio
import datetime
import json
import logging
import queue
import re
import uuid
from crewai import Crew, Process
from app.agents import make_planner_agent, make_analysis_agent, make_processing_agent
from app.tasks import make_plan_task, make_analysis_task, make_processing_task
from app.db import (
    get_keyframe_index,
    get_keyframe_indices_for_videos,
    get_unindexed_video_urls,
    get_session_assets,
    get_job_asset_manifest,
    create_output,
    create_session_asset,
    record_job_log,
    record_job_step,
    get_app_setting,
)
from app.generated_asset_store import write_generated_asset
from app.tools.catalogue import fetch_tool_catalogue, filter_catalogue_for_frontend, format_catalogue_for_planner, reset_analysis_rate_limiter
from app.tools.crewai_tools import build_crewai_tools, set_mcp_job_log_queue
from app.litellm_callbacks import set_job_context, clear_job_context, set_loop, clear_loop, drain_pending_logs, wrap_litellm_completion, guard_tool_usage_errors, _thread_local
from app.log_sequence import new_counter
from app.config import settings
from app.utils import extract_json_string

logger = logging.getLogger(__name__)


def _to_json_safe(obj):
    """Recursively convert asyncpg/Python types to JSON-serialisable primitives.

    CrewAI's kickoff() inputs dict only accepts str/int/float/bool/dict/list.
    asyncpg returns uuid.UUID and datetime objects which must be stringified.
    """
    if isinstance(obj, list):
        return [_to_json_safe(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (uuid.UUID, datetime.datetime, datetime.date)):
        return str(obj)
    return obj


async def _load_job_history(job_id: str | None) -> list[dict]:
    """Walk the parent_job_id chain and return manifests for all ancestor jobs.

    Returns a list ordered oldest-first (root ancestor first, direct parent last).
    Uses a visited set to prevent infinite loops if the chain ever forms a cycle.
    """
    if not job_id:
        return []

    history: list[dict] = []
    visited: set[str] = set()
    current_id: str | None = job_id

    while current_id and current_id not in visited:
        visited.add(current_id)
        manifest = await get_job_asset_manifest(current_id)
        if manifest is None:
            break
        history.append(manifest)
        current_id = manifest.get("parent_job_id")

    # Reverse so oldest ancestor is first
    history.reverse()
    return history


async def _get_video_duration(video_url: str) -> float:
    """Return video duration in seconds via ffprobe. Returns 0.0 on failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            video_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return float(stdout.decode().strip())
    except Exception:
        return 0.0


def _kickoff_with_context(
    crew: Crew,
    inputs: dict,
    job_id: str,
    session_id: str | None,
    loop: asyncio.AbstractEventLoop,
    tool_max_retry_limit: int = 5,
    planner_model: str | None = None,
    planner_rpm_limit: int | None = None,
):
    """Run crew.kickoff() inside the executor thread with job context set for callbacks.

    Monkey-patches litellm.completion for the duration of the kickoff so every
    LLM call — success or failure — is written to the DB in real-time via
    asyncio.run_coroutine_threadsafe().  Falls back to the pending-queue if no
    loop is available.

    tool_max_retry_limit: max consecutive ToolUsageErrors per tool before the
    recovery mechanism fires (guarded by guard_tool_usage_errors); also the maximum
    number of consecutive LLM calls without any successful tool use before
    LlmCyclingLimitExceeded is raised (guarded via _thread_local cycling counters
    in wrap_litellm_completion).

    planner_model: when a tool hits tool_max_retry_limit consecutive failures, all
    subsequent LLM calls switch to this model and all counters are reset. If the
    limit is reached again while on the planner model, ToolRetryLimitExceeded is raised.

    planner_rpm_limit: RPM cap enforced for LLM calls made in recovery mode (planner model).
    None means unlimited.
    """
    set_job_context(job_id, session_id)
    set_loop(loop)
    _thread_local.seq_counter = new_counter()
    _thread_local.llm_cycle_count = 0
    _thread_local.llm_cycle_limit = tool_max_retry_limit
    _thread_local.recovery_model = planner_model
    _thread_local.recovery_model_active = False
    _thread_local.planner_rpm_limit = planner_rpm_limit
    _thread_local.recovery_call_times = None  # initialised lazily as a deque on first use
    try:
        with wrap_litellm_completion():
            with guard_tool_usage_errors(limit=tool_max_retry_limit):
                return crew.kickoff(inputs=inputs)
    finally:
        clear_job_context()
        clear_loop()
        _thread_local.seq_counter = None
        _thread_local.llm_cycle_count = 0
        _thread_local.llm_cycle_limit = None
        _thread_local.recovery_model = None
        _thread_local.recovery_model_active = False
        _thread_local.planner_rpm_limit = None
        _thread_local.recovery_call_times = None


def _step_name_from_output(step_output, agent_role: str) -> str:
    """Extract a human-readable step name from a CrewAI step output."""
    try:
        raw = json.dumps(step_output, default=str)
        # Look for a tool name in the serialised output
        m = re.search(r'"tool"\s*:\s*"([^"]+)"', raw)
        if m:
            tool = m.group(1).replace("_", " ")
            return f"{agent_role}: called {tool}"
    except Exception:
        pass
    return f"{agent_role}: thinking"


def _make_step_callback(log_queue: queue.Queue, agent_role: str, loop: asyncio.AbstractEventLoop):
    """Return a step_callback that writes each agent ReAct iteration to the DB
    in real-time and also writes a progress row to job_steps."""
    def _callback(step_output):
        try:
            job_id = getattr(_thread_local, "job_id", None)
            if not job_id:
                return
            session_id = getattr(_thread_local, "session_id", None)
            counter = getattr(_thread_local, "seq_counter", None)
            seq_num = next(counter) if counter is not None else 0
            try:
                message = json.dumps(step_output, default=str)
            except Exception:
                message = str(step_output)
            log_entry = {
                "job_id": job_id,
                "session_id": session_id,
                "service_name": settings.service_name,
                "log_type": "agent_step",
                "agent_role": agent_role,
                "task_name": None,
                "model_id": None,
                "tool_name": None,
                "message": message,
                "message_type": "Output",
                "call_group_id": str(uuid.uuid4()),
                "sequence_num": seq_num,
                "error_text": None,
            }
            # Real-time DB write; fall back to queue on error
            try:
                asyncio.run_coroutine_threadsafe(
                    record_job_log(**log_entry),
                    loop,
                )
            except Exception:
                log_queue.put(log_entry)
            # Write progress to job_steps in real-time so the SSE stream can pick it up
            step_name = _step_name_from_output(step_output, agent_role)
            asyncio.run_coroutine_threadsafe(
                record_job_step(job_id, step_name),
                loop,
            )
        except Exception:
            logger.debug("_make_step_callback: failed to write step log", exc_info=True)
    return _callback


def _make_task_callback(log_queue: queue.Queue, agent_role: str, task_name: str, loop: asyncio.AbstractEventLoop):
    """Return a task callback that writes task completion to the DB in real-time
    and also writes a progress row to job_steps."""
    def _callback(task_output):
        try:
            job_id = getattr(_thread_local, "job_id", None)
            if not job_id:
                return
            session_id = getattr(_thread_local, "session_id", None)
            counter = getattr(_thread_local, "seq_counter", None)
            seq_num = next(counter) if counter is not None else 0
            try:
                message = str(task_output.raw) if hasattr(task_output, "raw") else str(task_output)
            except Exception:
                message = repr(task_output)
            log_entry = {
                "job_id": job_id,
                "session_id": session_id,
                "service_name": settings.service_name,
                "log_type": "task_complete",
                "agent_role": agent_role,
                "task_name": task_name,
                "model_id": None,
                "tool_name": None,
                "message": message,
                "message_type": "Output",
                "call_group_id": str(uuid.uuid4()),
                "sequence_num": seq_num,
                "error_text": None,
            }
            # Real-time DB write; fall back to queue on error
            try:
                asyncio.run_coroutine_threadsafe(
                    record_job_log(**log_entry),
                    loop,
                )
            except Exception:
                log_queue.put(log_entry)
            # Write task completion to job_steps in real-time
            asyncio.run_coroutine_threadsafe(
                record_job_step(job_id, f"{task_name} complete"),
                loop,
            )
        except Exception:
            logger.debug("_make_task_callback: failed to write task log", exc_info=True)
    return _callback


async def run_crew(
    prompt: str,
    video_urls: list[str],
    job_id: str,
    user_id: str,
    session_id: str | None = None,
    parent_job_id: str | None = None,
    extra_asset_urls: list[str] | None = None,
) -> str:
    """
    Kick off the CrewAI sequential pipeline.
    Returns the output blob URL of the compiled video.
    """
    # Normalise inputs
    if not video_urls:
        video_urls = []
    primary_video_url = video_urls[0] if video_urls else ""

    # Fetch all context concurrently (including agent_model from app_settings).
    # reset_analysis_rate_limiter() runs alongside the other init tasks so frontier-model
    # rate-limiting state from the previous job is cleared before this job's crew starts.
    gather_tasks = [
        get_keyframe_indices_for_videos(video_urls),
        _get_video_duration(primary_video_url) if primary_video_url else asyncio.sleep(0),
        fetch_tool_catalogue(),
        get_session_assets(session_id) if session_id else asyncio.sleep(0),
        _load_job_history(parent_job_id) if parent_job_id else asyncio.sleep(0),
        get_app_setting("agent_model"),
        get_app_setting("indexing_wait_attempts"),
        get_app_setting("agent_rpm_limit"),
        get_app_setting("planner_agent_model"),
        get_app_setting("planner_agent_rpm_limit"),
        get_app_setting("tool_max_retry_limit"),
        reset_analysis_rate_limiter(),
    ]
    results = await asyncio.gather(*gather_tasks, return_exceptions=True)

    keyframe_indices = results[0] if not isinstance(results[0], Exception) else {}
    video_duration = results[1] if not isinstance(results[1], Exception) else 0.0
    if not isinstance(video_duration, (int, float)):
        video_duration = 0.0
    tool_catalogue = results[2] if not isinstance(results[2], Exception) else []
    tool_catalogue = filter_catalogue_for_frontend(tool_catalogue)
    session_assets = results[3] if not isinstance(results[3], Exception) else []
    job_history: list[dict] = results[4] if not isinstance(results[4], Exception) and isinstance(results[4], list) else []
    db_agent_model = results[5] if not isinstance(results[5], Exception) else None
    agent_model = db_agent_model or settings.agent_model
    _db_indexing_wait = results[6] if not isinstance(results[6], Exception) else None
    try:
        _MAX_INDEXING_WAIT_ATTEMPTS = int(_db_indexing_wait) if _db_indexing_wait is not None else 600
    except (ValueError, TypeError):
        _MAX_INDEXING_WAIT_ATTEMPTS = 60
    _db_agent_rpm = results[7] if not isinstance(results[7], Exception) else None
    agent_rpm_limit: int | None
    if _db_agent_rpm is not None and _db_agent_rpm != "":
        try:
            _v = int(_db_agent_rpm)
            agent_rpm_limit = _v if _v > 0 else None
        except (ValueError, TypeError):
            agent_rpm_limit = settings.agent_rpm_limit
    else:
        agent_rpm_limit = settings.agent_rpm_limit

    _db_planner_model = results[8] if not isinstance(results[8], Exception) else None
    planner_model = _db_planner_model or agent_model
    _db_tool_max_retry = results[10] if not isinstance(results[10], Exception) else None
    try:
        tool_max_retry_limit = int(_db_tool_max_retry) if _db_tool_max_retry is not None else 5
    except (ValueError, TypeError):
        tool_max_retry_limit = 5

    _db_planner_rpm = results[9] if not isinstance(results[9], Exception) else None
    planner_rpm_limit: int | None
    if _db_planner_rpm is not None and _db_planner_rpm != "":
        try:
            _v = int(_db_planner_rpm)
            planner_rpm_limit = _v if _v > 0 else None
        except (ValueError, TypeError):
            planner_rpm_limit = agent_rpm_limit
    else:
        planner_rpm_limit = agent_rpm_limit

    # Merge any explicitly-passed asset URLs (used when no session_id is available)
    if extra_asset_urls:
        extra = [
            {"asset_type": "uploaded_file", "blob_url": url, "filename": url.split("/")[-1]}
            for url in extra_asset_urls
        ]
        if isinstance(session_assets, list):
            session_assets = list(session_assets) + extra
        else:
            session_assets = extra

    # If the session exists but has no assets yet, the preprocessing worker may still
    # be running (user submitted prompt immediately after upload).  Retry up to 30 s.
    if session_id and (not isinstance(session_assets, list) or not session_assets):
        for attempt in range(1, 7):
            logger.info(
                "run_crew: session %s has no assets yet — waiting for preprocessing (attempt %d/6)",
                session_id, attempt,
            )
            await asyncio.sleep(5)
            session_assets = await get_session_assets(session_id)
            if isinstance(session_assets, list) and session_assets:
                logger.info("run_crew: session assets now available after %d wait(s)", attempt)
                break

    logger.info(
        "run_crew: session_id=%s video_urls_from_request=%s session_assets_count=%s",
        session_id,
        video_urls,
        len(session_assets) if isinstance(session_assets, list) else repr(session_assets),
    )
    if isinstance(session_assets, list):
        for a in session_assets:
            logger.info("  session_asset: type=%s blob_url=%s", a.get("asset_type"), a.get("blob_url"))

    # If no video_urls were passed explicitly, extract them from session assets.
    # This is the common path when the request comes via LibreChat (no video_urls
    # in the body) and the video was uploaded through the Angular shell.
    if not video_urls and isinstance(session_assets, list):
        video_urls = [
            a["blob_url"]
            for a in session_assets
            if a.get("asset_type") == "uploaded_video" and a.get("blob_url")
        ]
        primary_video_url = video_urls[0] if video_urls else ""
        logger.info("run_crew: extracted video_urls from session_assets: %s", video_urls)
        # Fetch keyframe indices for the session videos now that we know the URLs
        if video_urls:
            keyframe_indices = await get_keyframe_indices_for_videos(video_urls)

    # Wait until all videos reach status='indexed' before proceeding.
    # The preprocessing worker writes all keyframe rows first, then sets status='indexed',
    # so this guarantees the keyframe index is complete before the crew starts.
    # The API gateway creates session_assets immediately on upload (before preprocessing),
    # so the session_assets check above is not sufficient — videos can have an
    # uploaded_video session_asset row while their keyframes are still being written.
    if video_urls:
        unindexed: list[str] = []
        for attempt in range(1, _MAX_INDEXING_WAIT_ATTEMPTS + 1):
            unindexed = await get_unindexed_video_urls(video_urls)
            if not unindexed:
                break
            logger.info(
                "run_crew: %d video(s) not yet indexed — waiting for preprocessing "
                "(attempt %d/%d): %s",
                len(unindexed), attempt, _MAX_INDEXING_WAIT_ATTEMPTS, unindexed,
            )
            await asyncio.sleep(5)
        else:
            logger.warning(
                "run_crew: preprocessing did not complete within %d s for %d video(s): %s — "
                "proceeding with partial keyframe index; analysis tools may return empty results",
                _MAX_INDEXING_WAIT_ATTEMPTS * 5,
                len(unindexed),
                unindexed,
            )
        # Re-fetch now that preprocessing is complete (or timed out)
        keyframe_indices = await get_keyframe_indices_for_videos(video_urls)
        logger.info(
            "run_crew: keyframe indices fetched — %s",
            {url: len(frames) for url, frames in keyframe_indices.items()},
        )

    # # Drop any videos whose keyframe index is still empty (preprocessing timed out or failed).
    # # Keeping them in video_urls causes extract_frames to return zero frames, which confuses
    # # the planner into producing an empty extraction plan for those videos.
    # indexable_video_urls = [u for u in video_urls if keyframe_indices.get(u)]
    # if len(indexable_video_urls) < len(video_urls):
    #     skipped = [u for u in video_urls if u not in indexable_video_urls]
    #     logger.warning(
    #         "run_crew: excluding %d video(s) with no keyframe index "
    #         "(preprocessing incomplete or timed out): %s",
    #         len(skipped), skipped,
    #     )
    #     video_urls = indexable_video_urls
    #     primary_video_url = video_urls[0] if video_urls else ""

    # Write each video's keyframe index to blob; pass only asset paths to tasks/LLMs.
    keyframe_index_assets: dict[str, str] = {}
    for video_url in video_urls:
        frames = keyframe_indices.get(video_url, [])
        if frames:
            video_id_short = str(uuid.uuid5(uuid.NAMESPACE_URL, video_url)).replace("-", "")[:8]
            try:
                blob_url = await write_generated_asset(
                    session_id=session_id,
                    job_id=job_id,
                    data_type="keyframe-index",
                    filename=f"video_{video_id_short}.json",
                    data=frames,
                )
                keyframe_index_assets[video_url] = blob_url
                logger.info(
                    "run_crew: wrote keyframe index for %s -> %s (%d frames)",
                    video_url, blob_url, len(frames),
                )
            except Exception as exc:
                logger.warning(
                    "run_crew: could not write keyframe index blob for %s: %s", video_url, exc
                )

    tool_catalogue_text = format_catalogue_for_planner(tool_catalogue)

    # Build CrewAI tool wrappers from the catalogue and assign by server type.
    # Filter by each tool's own _server_url so tools registered on both servers
    # (e.g. query_asset) are correctly assigned to both agents.
    all_tools = build_crewai_tools(tool_catalogue) if isinstance(tool_catalogue, list) else []
    analysis_tools = [t for t in all_tools if "analysis" in (t._server_url or "")]
    processing_tools = [t for t in all_tools if "processing" in (t._server_url or "")]

    # Set up per-kickoff queue before creating agents so step/task callbacks can close over it
    mcp_log_queue: queue.Queue = queue.Queue()
    set_mcp_job_log_queue(mcp_log_queue)

    # Capture the running event loop so callbacks can schedule async DB writes from threads
    loop = asyncio.get_running_loop()

    planner = make_planner_agent(model=planner_model, rpm_limit=planner_rpm_limit)
    analyst = make_analysis_agent(model=agent_model, tools=analysis_tools, rpm_limit=agent_rpm_limit)
    processor = make_processing_agent(model=agent_model, tools=processing_tools, rpm_limit=agent_rpm_limit)

    logger.info(
        "run_crew: planner_model=%s planner_rpm_limit=%s agent_model=%s agent_rpm_limit=%s | planner._rpm_controller=%r | analyst._rpm_controller=%r | processor._rpm_controller=%r",
        planner_model,
        planner_rpm_limit,
        agent_model,
        agent_rpm_limit,
        planner._rpm_controller,
        analyst._rpm_controller,
        processor._rpm_controller,
    )

    # Attach per-agent step callbacks — log each ReAct iteration and write job_steps in real-time
    planner.step_callback = _make_step_callback(mcp_log_queue, planner.role, loop)
    analyst.step_callback = _make_step_callback(mcp_log_queue, analyst.role, loop)
    processor.step_callback = _make_step_callback(mcp_log_queue, processor.role, loop)

    plan_task = make_plan_task(
        planner,
        prompt=prompt,
        video_urls=video_urls,
        video_duration_seconds=video_duration,
        tool_catalogue_text=tool_catalogue_text,
        session_assets=session_assets,
        job_history=job_history,
        keyframe_index_assets=keyframe_index_assets,
    )
    analysis_task = make_analysis_task(
        analyst,
        video_urls=video_urls,
        job_id=job_id,
        session_id=session_id,
        keyframe_index_assets=keyframe_index_assets,
        tool_catalogue=tool_catalogue,
    )
    processing_task = make_processing_task(processor, job_id=job_id, user_id=user_id)

    # Attach per-task completion callbacks — log when each task finishes and write job_steps
    plan_task.callback = _make_task_callback(mcp_log_queue, planner.role, "Planning", loop)
    analysis_task.callback = _make_task_callback(mcp_log_queue, analyst.role, "Analysis", loop)
    processing_task.callback = _make_task_callback(mcp_log_queue, processor.role, "Processing", loop)

    crew = Crew(
        agents=[planner, analyst, processor],
        tasks=[plan_task, analysis_task, processing_task],
        process=Process.sequential,
        memory=False,
        verbose=(settings.log_level.upper() == "DEBUG"),
    )

    # CrewAI kickoff is synchronous; run in executor to avoid blocking the event loop
    # CrewAI kickoff only accepts str/int/float/bool/dict/list — convert UUID/datetime
    kickoff_inputs = _to_json_safe({
        "prompt": prompt,
        "video_url": primary_video_url,
        "video_urls": video_urls,
        "video_duration_seconds": video_duration,
        "keyframe_index_assets": keyframe_index_assets,
        "job_id": job_id,
        "user_id": user_id,
        "session_id": session_id or "",
        "parent_job_id": parent_job_id or "",
        "job_history": job_history,
        "session_assets": session_assets if isinstance(session_assets, list) else [],
        "tool_catalogue": tool_catalogue if isinstance(tool_catalogue, list) else [],
    })

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: _kickoff_with_context(crew, kickoff_inputs, job_id, session_id, loop, tool_max_retry_limit, planner_model, planner_rpm_limit),
        )
    finally:
        set_mcp_job_log_queue(None)

        # Drain both queues regardless of success/failure so logs are always recorded.
        # This must be in the finally block — if crew.kickoff() raises, the code
        # after the try/finally is never reached.
        for log_entry in drain_pending_logs():
            try:
                await record_job_log(
                    job_id=log_entry.get("job_id") or job_id,
                    session_id=log_entry.get("session_id") or session_id,
                    service_name=log_entry.get("service_name", settings.service_name),
                    log_type=log_entry.get("log_type", "llm_call"),
                    model_id=log_entry.get("model_id"),
                    tool_name=log_entry.get("tool_name"),
                    agent_role=log_entry.get("agent_role"),
                    task_name=log_entry.get("task_name"),
                    message=log_entry.get("message"),
                    message_type=log_entry.get("message_type", "Output"),
                    call_group_id=log_entry.get("call_group_id"),
                    sequence_num=log_entry.get("sequence_num", 0),
                    error_text=log_entry.get("error_text"),
                )
            except Exception:
                logger.warning("Could not record agent job log", exc_info=True)

        while not mcp_log_queue.empty():
            try:
                log_data = mcp_log_queue.get_nowait()
                # Frontier tool logs may arrive as a single dict with both
                # seq_input/seq_output — split them into two rows.
                if "seq_input" in log_data and "seq_output" in log_data:
                    fg_id = log_data.get("call_group_id", str(uuid.uuid4()))
                    base = dict(
                        job_id=log_data.get("job_id") or job_id,
                        session_id=log_data.get("session_id") or session_id,
                        service_name=log_data.get("service_name", "unknown"),
                        log_type=log_data.get("log_type", "llm_call"),
                        model_id=log_data.get("model_id"),
                        tool_name=log_data.get("tool_name"),
                        agent_role=log_data.get("agent_role"),
                        task_name=log_data.get("task_name"),
                        call_group_id=fg_id,
                        error_text=log_data.get("error_text"),
                    )
                    await record_job_log(
                        **base,
                        message=log_data.get("input_data"),
                        message_type="Input",
                        sequence_num=log_data["seq_input"],
                    )
                    await record_job_log(
                        **base,
                        message=log_data.get("output_data"),
                        message_type="Output",
                        sequence_num=log_data["seq_output"],
                    )
                else:
                    await record_job_log(
                        job_id=log_data.get("job_id") or job_id,
                        session_id=log_data.get("session_id") or session_id,
                        service_name=log_data.get("service_name", "unknown"),
                        log_type=log_data.get("log_type", "tool_call"),
                        model_id=log_data.get("model_id"),
                        tool_name=log_data.get("tool_name"),
                        agent_role=log_data.get("agent_role"),
                        task_name=log_data.get("task_name"),
                        message=log_data.get("message"),
                        message_type=log_data.get("message_type", "Output"),
                        call_group_id=log_data.get("call_group_id"),
                        sequence_num=log_data.get("sequence_num", 0),
                        error_text=log_data.get("error_text"),
                    )
            except Exception:
                logger.warning("Could not record MCP job log", exc_info=True)

    # The processing agent returns the output blob URL (possibly JSON with multiple outputs)
    output_url = _parse_output_url(str(result).strip())

    # Register output in DB — only when there is a real video URL to store.
    # When the pipeline found no matching segments it returns output_url=None;
    # skip DB registration in that case so follow-up jobs never receive a
    # null/broken URL in the job history context.
    if session_id and output_url:
        try:
            output_id = await create_output(
                job_id=job_id,
                session_id=session_id,
                blob_url=output_url,
                filename=f"job_{job_id}_output.mp4",
                content_type="video/mp4",
            )
            await create_session_asset(
                session_id=session_id,
                asset_type="job_output_video",
                blob_url=output_url,
                source_id=output_id,
                filename=f"job_{job_id}_output.mp4",
                content_type="video/mp4",
                label=f"job:{job_id}",
                description=f"Final compiled video output — job prompt: \"{prompt[:80]}\"",
            )
        except Exception as exc:
            logger.warning("Could not register output in session_assets: %s", exc)

    # Register analysis tool result assets in session_assets so follow-up jobs can reuse them
    if session_id:
        try:
            analysis_raw = getattr(getattr(analysis_task, "output", None), "raw", None) or ""
            await _register_analysis_assets(job_id, session_id, analysis_raw)
        except Exception as exc:
            logger.warning("Could not register analysis assets: %s", exc)

    return output_url


def _describe_analysis_asset(tool_name: str, filename: str, summary: dict | None) -> str:
    """Generate a human-readable description for an analysis result asset."""
    if "segment" in filename.lower():
        n = (summary or {}).get("segments_count", len((summary or {}).get("segments", [])))
        return f"Merged segments list — {n} time intervals. Pass as segments_asset to extract_clips_bulk."
    if tool_name == "detect_objects":
        classes = (summary or {}).get("classes_detected", [])
        n_segs = len((summary or {}).get("segments", []))
        n_det = (summary or {}).get("total_detections", 0)
        cls_str = ", ".join(classes) if classes else "unknown"
        return f"YOLO object detection for [{cls_str}] — {n_segs} segments, {n_det} detections"
    if tool_name == "detect_objects_vision":
        n_segs = len((summary or {}).get("segments", []))
        return f"Claude vision object detection — {n_segs} segments detected"
    if tool_name == "detect_motion":
        n_segs = len((summary or {}).get("segments", []))
        return f"Motion detection (optical flow) — {n_segs} high-motion segments"
    if tool_name == "detect_motion_sports":
        n_segs = len((summary or {}).get("segments", []))
        return f"Sports motion detection — {n_segs} events detected"
    if tool_name == "analyze_scene":
        n_frames = (summary or {}).get("frames_analyzed", 0)
        return f"Scene analysis (Claude vision) — {n_frames} frames described"
    if tool_name == "estimate_height_above_surface":
        n_events = len((summary or {}).get("segments", []))
        peak = (summary or {}).get("peak_height_m", 0.0)
        return f"Height above surface (Depth Anything V2) — {n_events} airborne events, peak {peak:.2f} m"
    if tool_name == "transcribe_audio":
        return "Audio transcription (Whisper)"
    if tool_name == "extract_frames":
        n = (summary or {}).get("frame_count", 0)
        return f"Extracted keyframes — {n} frames"
    return f"{tool_name} result"


async def _register_analysis_assets(job_id: str, session_id: str, analysis_raw: str) -> None:
    """Parse analysis task output and register every result asset in session_assets.

    Extracts result blobs from detection_assets, frame_assets, transcription_assets,
    and segments_asset in the analysis task JSON output, then writes each as a
    job_analysis_result row so follow-up jobs can discover and reuse them.
    """
    if not analysis_raw:
        return
    try:
        data = json.loads(extract_json_string(analysis_raw))
    except Exception:
        logger.debug("_register_analysis_assets: could not parse analysis output JSON")
        return
    if not isinstance(data, dict):
        return

    # Collect (tool_name, blob_url, summary) tuples from all output fields
    entries: list[tuple[str, str, dict | None]] = []

    for item in data.get("detection_assets", []):
        url = item.get("result_asset") or item.get("blob_url") or ""
        if url:
            entries.append((item.get("tool_name", "detection"), url, item.get("summary")))

    for item in data.get("frame_assets", []):
        url = item.get("result_asset") or item.get("blob_url") or ""
        if url:
            entries.append(("extract_frames", url, item.get("summary")))

    for item in data.get("transcription_assets", []):
        url = item.get("result_asset") or item.get("blob_url") or ""
        if url:
            entries.append(("transcribe_audio", url, item.get("summary")))

    seg_url = data.get("segments_asset")
    if seg_url and isinstance(seg_url, str):
        seg_count = data.get("segments_count", 0)
        entries.append(("write_segments_asset", seg_url, {"segments_count": seg_count}))

    for tool_name, blob_url, summary in entries:
        try:
            filename = blob_url.rstrip("/").split("/")[-1] or f"{tool_name}_result.json"
            description = _describe_analysis_asset(tool_name, filename, summary)
            await create_session_asset(
                session_id=session_id,
                asset_type="job_analysis_result",
                blob_url=blob_url,
                source_id=str(uuid.uuid4()),
                filename=filename,
                content_type="application/json",
                source_job_id=job_id,
                description=description,
                summary_json=summary,
            )
            logger.debug("_register_analysis_assets: registered %s (%s)", filename, tool_name)
        except Exception as exc:
            logger.warning("_register_analysis_assets: could not register %s: %s", blob_url, exc)


def _parse_output_url(result: str) -> str | None:
    """Extract output URL from result string, which may be plain URL or JSON.

    Returns None when the processing agent reports no_matching_segments
    (i.e. output_url key is present in the JSON but its value is null).
    This prevents the raw JSON string from being stored as the job output_url.
    """
    try:
        data = json.loads(extract_json_string(result))
        if isinstance(data, dict):
            if "output_url" in data:
                # Explicit key present — return value or None (never the raw JSON string)
                url = data["output_url"]
                return url if url else None
            url = data.get("url")
            return url if url else None
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                url = first.get("output_url") or first.get("url")
                return url if url else None
            return str(first)
    except (json.JSONDecodeError, KeyError, ValueError):
        pass
    return result


async def get_keyframe_index_for_video(video_url: str) -> list[dict]:
    """Retrieve keyframe index from DB for a given video URL (single-video compat helper)."""
    try:
        return await get_keyframe_index(video_url)
    except Exception:
        return []
