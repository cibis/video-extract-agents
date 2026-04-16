"""
E2E test: analyze_scene (Claude vision, frontier) pipeline.

Exercises the full pipeline for semantic scene understanding:
  upload → preprocess → planner selects analyze_scene → Claude vision batched
  scene descriptions → scene-based segment assembly → clip extraction → output.

Skipped if credentials for the configured tool_frontier_model are absent.
"""
import pytest

from tests.e2e import video_factory
from tests.e2e.helpers import (
    assert_job_succeeded,
    assert_tool_invoked,
    create_test_session,
    submit_job,
    upload_video,
    wait_for_indexed,
    wait_for_job,
)


def test_analyze_scene_pipeline(request, frontier_model_available, tmp_path, api_gateway_url, http_client, auth_headers):
    if not frontier_model_available:
        pytest.skip("Frontier model credentials not configured for the active tool_frontier_model")
    """
    Full pipeline test for the analyze_scene tool (Claude vision).

    Uses a prompt that explicitly requests scene description and semantic
    understanding — language that steers the planner toward analyze_scene
    rather than any motion or object detection tool.

    1. Generate a motion test-card video (richer visual variation per frame).
    2. Create a session and upload the video.
    3. Wait for keyframe indexing.
    4. Submit a job requesting semantic scene extraction.
    5. Poll until the job completes (extra timeout for Claude API calls).
    6. Assert the job succeeded.
    7. Assert analyze_scene was invoked.
    """
    # 1. Generate video — animated test card provides visual variety across frames
    video_path = str(tmp_path / "scene.mp4")
    video_factory.make_motion_video(video_path)

    # 2. Create session + upload
    session_id = create_test_session(http_client, api_gateway_url, auth_headers)

    video_id, _ = upload_video(
        http_client, api_gateway_url, auth_headers, session_id, video_path
    )

    # 3. Wait for preprocessing
    wait_for_indexed(http_client, api_gateway_url, auth_headers, session_id)

    # 4. Submit job — explicit question drives planner to pass question= to analyze_scene
    job = submit_job(
        http_client, api_gateway_url, auth_headers,
        video_id=video_id, session_id=session_id,
        prompt=(
            "This is a synthetic test card video. Find all segments where vertical color bars "
            "are arranged across the frame and compile them into a clip. "
            "Ask each frame: 'Does this frame contain vertical color bars?'"
        ),
        test_name=request.node.nodeid,
    )
    job_id = job["jobId"]

    # 5. Poll to completion — frontier calls may take longer
    job = wait_for_job(http_client, api_gateway_url, auth_headers, job_id, timeout=300)

    # 6. Assert pipeline succeeded
    assert_job_succeeded(job)

    # 7. Assert scene analysis tool was invoked
    assert_tool_invoked(
        http_client, api_gateway_url, auth_headers, job_id, "analyze_scene"
    )

    # 8. Verify matched_count is present in the analyze_scene summary logged for this job.
    #    When analyze_scene is called with a question, the summary includes matched_count.
    logs_resp = http_client.get(
        f"{api_gateway_url}/v1/jobs/{job_id}/logs",
        headers=auth_headers,
    )
    logs_resp.raise_for_status()
    logs = logs_resp.json().get("logs", [])
    scene_logs = [
        log for log in logs
        if log.get("tool_name") == "analyze_scene" and log.get("message")
    ]
    assert any(
        "matched_count" in (log.get("message") or "") for log in scene_logs
    ), (
        "Expected analyze_scene summary to contain matched_count when question is provided. "
        f"analyze_scene log messages: {[log.get('message') for log in scene_logs]}"
    )
