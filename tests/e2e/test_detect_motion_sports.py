"""
E2E test: detect_motion_sports pipeline.

Exercises the full pipeline for sports-action video extraction:
  upload → preprocess → planner selects detect_motion_sports → optical flow
  event detection → segment extraction → clip merge → output registered in DB.
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


def test_detect_motion_sports_pipeline(tmp_path, api_gateway_url, http_client, auth_headers):
    """
    Full pipeline test for the detect_motion_sports tool.

    1. Generate a synthetic high-contrast motion video (FFmpeg testsrc2).
    2. Create a session and upload the video.
    3. Wait for the preprocessing worker to index keyframes.
    4. Submit a job with a sports-specific prompt that steers the planner
       toward detect_motion_sports rather than the general detect_motion.
    5. Poll until the job completes.
    6. Assert the job succeeded.
    7. Assert detect_motion_sports was invoked via job logs.
    """
    # 1. Generate video — testsrc2 produces faster-moving high-contrast patterns
    video_path = str(tmp_path / "sports.mp4")
    video_factory.make_sports_video(video_path)

    # 2. Create session + upload
    session_id = create_test_session(http_client, api_gateway_url, auth_headers)

    video_id, _ = upload_video(
        http_client, api_gateway_url, auth_headers, session_id, video_path
    )

    # 3. Wait for preprocessing
    wait_for_indexed(http_client, api_gateway_url, auth_headers, session_id)

    # 4. Submit job — sports-specific language targets detect_motion_sports
    job_resp = http_client.post(
        f"{api_gateway_url}/v1/jobs",
        json={
            "videoId": video_id,
            "sessionId": session_id,
            "prompt": "Extract all segments containing jumps and high-intensity tricks",
        },
        headers=auth_headers,
    )
    job_resp.raise_for_status()
    job_id = job_resp.json()["jobId"]

    # 5. Poll to completion
    job = wait_for_job(http_client, api_gateway_url, auth_headers, job_id)

    # 6. Assert pipeline succeeded
    assert_job_succeeded(job)

    # 7. Assert the sports-specialised tool was selected
    assert_tool_invoked(
        http_client, api_gateway_url, auth_headers, job_id, "detect_motion_sports"
    )
