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
    upload_video,
    wait_for_indexed,
    wait_for_job,
)


def test_analyze_scene_pipeline(frontier_model_available, tmp_path, api_gateway_url, http_client, auth_headers):
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

    # 4. Submit job — scene description language routes to analyze_scene
    job_resp = http_client.post(
        f"{api_gateway_url}/v1/jobs",
        json={
            "videoId": video_id,
            "sessionId": session_id,
            "prompt": (
                "Extract the segments containing vertical color bars."
                "Use visual scene analysis to ask if the scene contains color bars. "
                "Do not use any detect_* tools — use analyze_scene  to understand the visual content. "
                
            ),
        },
        headers=auth_headers,
    )
    job_resp.raise_for_status()
    job_id = job_resp.json()["jobId"]

    # 5. Poll to completion — frontier calls may take longer
    job = wait_for_job(http_client, api_gateway_url, auth_headers, job_id, timeout=300)

    # 6. Assert pipeline succeeded
    assert_job_succeeded(job)

    # 7. Assert scene analysis tool was invoked
    assert_tool_invoked(
        http_client, api_gateway_url, auth_headers, job_id, "analyze_scene"
    )
