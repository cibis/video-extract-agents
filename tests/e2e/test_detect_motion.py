"""
E2E test: detect_motion pipeline.

Exercises the full pipeline for motion-based video extraction:
  upload → preprocess → planner selects detect_motion → optical flow analysis
  → segment extraction → clip merge → output registered in DB.
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


def test_detect_motion_pipeline(tmp_path, api_gateway_url, http_client, auth_headers):
    """
    Full pipeline test for the detect_motion tool.

    1. Generate a synthetic motion video (FFmpeg testsrc — moving test card).
    2. Create a session and upload the video.
    3. Wait for the preprocessing worker to index keyframes (VIDEO_INDEXED).
    4. Submit a job with a prompt that steers the planner toward detect_motion.
    5. Poll until the job completes.
    6. Assert the job succeeded (output_url set or no_matching_segments).
    7. Assert detect_motion was invoked via job logs.
    """
    # 1. Generate video
    video_path = str(tmp_path / "motion.mp4")
    video_factory.make_motion_video(video_path)

    # 2. Create session + upload
    session_id = create_test_session(http_client, api_gateway_url, auth_headers)

    video_id, _ = upload_video(
        http_client, api_gateway_url, auth_headers, session_id, video_path
    )

    # 3. Wait for preprocessing
    wait_for_indexed(http_client, api_gateway_url, auth_headers, session_id)

    # 4. Submit job — prompt targets general motion segments
    job_resp = http_client.post(
        f"{api_gateway_url}/v1/jobs",
        json={
            "videoId": video_id,
            "sessionId": session_id,
            "prompt": "Extract all segments with significant movement and motion",
        },
        headers=auth_headers,
    )
    job_resp.raise_for_status()
    job_id = job_resp.json()["jobId"]

    # 5. Poll to completion
    job = wait_for_job(http_client, api_gateway_url, auth_headers, job_id)

    # 6. Assert pipeline succeeded
    assert_job_succeeded(job)

    # 7. Assert a motion detection tool was selected by the planner.
    # Both detect_motion and detect_motion_sports are valid — the planner may
    # choose either depending on prompt interpretation.
    resp = http_client.get(
        f"{api_gateway_url}/v1/jobs/{job_id}/logs", headers=auth_headers
    )
    resp.raise_for_status()
    tool_names = [log.get("tool_name") for log in resp.json().get("logs", []) if log.get("tool_name")]
    assert any(t in tool_names for t in ("detect_motion", "detect_motion_sports")), (
        f"Expected detect_motion or detect_motion_sports in logs for job {job_id}. "
        f"Tools invoked: {tool_names}"
    )
