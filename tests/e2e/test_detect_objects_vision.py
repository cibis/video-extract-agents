"""
E2E test: detect_objects_vision (Claude vision, frontier) pipeline.

Exercises the full pipeline for open-vocabulary object detection via Claude:
  upload → preprocess → planner selects detect_objects_vision → Claude vision
  API batched inference → segment assembly → clip extraction → output registered.

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


def test_detect_objects_vision_pipeline(frontier_model_available, tmp_path, api_gateway_url, http_client, auth_headers):
    if not frontier_model_available:
        pytest.skip("Frontier model credentials not configured for the active tool_frontier_model")
    """
    Full pipeline test for the detect_objects_vision tool (Claude vision).

    Uses a prompt that describes a non-COCO target (colourful geometric patterns)
    to steer the planner away from YOLOv8n and toward the frontier vision tool.

    1. Generate a static solid-colour video (minimal frames, lower API cost).
    2. Create a session and upload the video.
    3. Wait for keyframe indexing.
    4. Submit a job with an open-vocabulary object description.
    5. Poll until the job completes.
    6. Assert the job succeeded.
    7. Assert detect_objects_vision was invoked.
    """
    # 1. Generate video — solid blue field, minimal API tokens per frame
    video_path = str(tmp_path / "vision_objects.mp4")
    video_factory.make_static_video(video_path)

    # 2. Create session + upload
    session_id = create_test_session(http_client, api_gateway_url, auth_headers)

    video_id, _ = upload_video(
        http_client, api_gateway_url, auth_headers, session_id, video_path
    )

    # 3. Wait for preprocessing
    wait_for_indexed(http_client, api_gateway_url, auth_headers, session_id)

    # 4. Submit job — open-vocabulary description not in COCO; routes to vision tool
    job_resp = http_client.post(
        f"{api_gateway_url}/v1/jobs",
        json={
            "videoId": video_id,
            "sessionId": session_id,
            "prompt": (
                "Extract all segments containing colourful geometric patterns "
                "or abstract shapes — use any vision-based detection except analyze_scene"
            ),
        },
        headers=auth_headers,
    )
    job_resp.raise_for_status()
    job_id = job_resp.json()["jobId"]

    # 5. Poll to completion (allow extra time for Claude API calls)
    job = wait_for_job(http_client, api_gateway_url, auth_headers, job_id, timeout=240)

    # 6. Assert pipeline succeeded
    assert_job_succeeded(job)

    # 7. Assert Claude vision tool was invoked
    assert_tool_invoked(
        http_client, api_gateway_url, auth_headers, job_id, "detect_objects_vision"
    )
