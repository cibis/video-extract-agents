"""
E2E test: detect_objects (YOLOv8n) pipeline.

Exercises the full pipeline for YOLO-based object detection:
  upload → preprocess → planner selects detect_objects → YOLOv8n inference
  → segment filtering → clip extraction → output registered in DB.

Note on expected results:
  The test video is a synthetic FFmpeg test card (testsrc). YOLOv8n is trained
  on COCO classes and will likely return 0 detections for abstract test patterns.
  The test therefore accepts EITHER:
    - A successful output (output_url set) if any COCO object is detected, OR
    - A 'no_matching_segments' completed result if nothing is found.
  Both outcomes confirm the full pipeline executed correctly. The assertion in
  assert_job_succeeded() covers both cases.
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


def test_detect_objects_pipeline(request, tmp_path, api_gateway_url, http_client, auth_headers):
    """
    Full pipeline test for the detect_objects tool (YOLOv8n).

    1. Generate a synthetic test-card video (FFmpeg testsrc).
    2. Create a session and upload the video.
    3. Wait for the preprocessing worker to index keyframes.
    4. Submit a job requesting object-based extraction (person class).
    5. Poll until the job completes.
    6. Assert the job succeeded (output or no_matching_segments — both valid).
    7. Assert detect_objects was invoked via job logs.
    """
    # 1. Generate video
    video_path = str(tmp_path / "objects.mp4")
    video_factory.make_object_video(video_path)

    # 2. Create session + upload
    session_id = create_test_session(http_client, api_gateway_url, auth_headers)

    video_id, _ = upload_video(
        http_client, api_gateway_url, auth_headers, session_id, video_path
    )

    # 3. Wait for preprocessing
    wait_for_indexed(http_client, api_gateway_url, auth_headers, session_id)

    # 4. Submit job — standard COCO class ("person") targets detect_objects
    job = submit_job(
        http_client, api_gateway_url, auth_headers,
        video_id=video_id, session_id=session_id,
        prompt="Extract all segments containing a person",
        test_name=request.node.nodeid,
    )
    job_id = job["jobId"]

    # 5. Poll to completion
    job = wait_for_job(http_client, api_gateway_url, auth_headers, job_id)

    # 6. Assert pipeline succeeded (0 detections is also a valid result)
    assert_job_succeeded(job)

    # 7. Assert YOLO object detection was invoked
    assert_tool_invoked(http_client, api_gateway_url, auth_headers, job_id, "detect_objects")
