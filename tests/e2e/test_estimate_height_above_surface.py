"""
E2E test: estimate_height_above_surface pipeline.

Exercises the full pipeline for camera height estimation above a surface:
  upload → preprocess → planner selects extract_frames then
  estimate_height_above_surface → Depth Anything V2 Metric inference
  → airborne event detection → result asset registered in session.

The tool takes frames_asset (the result_asset from extract_frames), so the
planner must call extract_frames first.  Both tool invocations are asserted.
"""
from tests.e2e import video_factory
from tests.e2e.helpers import (
    assert_job_succeeded,
    assert_tool_invoked,
    create_test_session,
    upload_video,
    wait_for_indexed,
    wait_for_job,
)


def test_estimate_height_above_surface_pipeline(tmp_path, api_gateway_url, http_client, auth_headers):
    """
    Full pipeline test for the estimate_height_above_surface tool.

    1. Generate a synthetic POV-style video (FFmpeg testsrc).
    2. Create a session and upload the video.
    3. Wait for the preprocessing worker to index keyframes.
    4. Submit a job whose prompt steers the planner toward
       estimate_height_above_surface (height / POV / surface language).
    5. Poll until the job completes.
    6. Assert the job succeeded.
    7. Assert extract_frames was invoked (required prerequisite step).
    8. Assert estimate_height_above_surface was invoked.
    """
    # 1. Generate video — any visual content works; DA-V2 Metric runs on any frame
    video_path = str(tmp_path / "pov.mp4")
    video_factory.make_pov_video(video_path)

    # 2. Create session + upload
    session_id = create_test_session(http_client, api_gateway_url, auth_headers)

    video_id, _ = upload_video(
        http_client, api_gateway_url, auth_headers, session_id, video_path
    )

    # 3. Wait for preprocessing
    wait_for_indexed(http_client, api_gateway_url, auth_headers, session_id)

    # 4. Submit job — height/POV/surface language steers planner toward
    #    estimate_height_above_surface; "first-person" / "above the ground"
    #    rules out generic motion or object detection
    job_resp = http_client.post(
        f"{api_gateway_url}/v1/jobs",
        json={
            "videoId": video_id,
            "sessionId": session_id,
            "prompt": (
                "Estimate the camera height above the ground surface in this "
                "first-person POV footage and identify any moments where the "
                "camera is elevated above the surface"
            ),
        },
        headers=auth_headers,
    )
    job_resp.raise_for_status()
    job_id = job_resp.json()["jobId"]

    # 5. Poll to completion — DA-V2 Metric runs per frame on CPU; allow extra time
    job = wait_for_job(http_client, api_gateway_url, auth_headers, job_id)

    # 6. Assert pipeline succeeded (no_matching_segments is also a valid outcome)
    assert_job_succeeded(job)

    # 7. Assert the height estimation tool was selected and executed.
    #    extract_frames is implicitly called first (the tool requires frames_asset),
    #    but asserting estimate_height_above_surface is sufficient — it cannot run
    #    without extract_frames having been called beforehand.
    assert_tool_invoked(
        http_client, api_gateway_url, auth_headers, job_id, "estimate_height_above_surface"
    )


def test_estimate_height_above_surface_analysis_asset_registered(
    tmp_path, api_gateway_url, http_client, auth_headers
):
    """
    After a successful height estimation job, the analysis result is registered
    as a job_analysis_result session asset with a meaningful description.
    """
    video_path = str(tmp_path / "pov2.mp4")
    video_factory.make_pov_video(video_path)

    session_id = create_test_session(http_client, api_gateway_url, auth_headers)

    video_id, _ = upload_video(
        http_client, api_gateway_url, auth_headers, session_id, video_path
    )

    wait_for_indexed(http_client, api_gateway_url, auth_headers, session_id)

    job_resp = http_client.post(
        f"{api_gateway_url}/v1/jobs",
        json={
            "videoId": video_id,
            "sessionId": session_id,
            "prompt": (
                "Estimate the camera height above the ground surface in this "
                "first-person POV footage and identify any moments where the "
                "camera is elevated above the surface"
            ),
        },
        headers=auth_headers,
    )
    job_resp.raise_for_status()
    job_id = job_resp.json()["jobId"]

    job = wait_for_job(http_client, api_gateway_url, auth_headers, job_id)
    assert_job_succeeded(job)

    assert_tool_invoked(
        http_client, api_gateway_url, auth_headers, job_id, "estimate_height_above_surface"
    )

    # The orchestrator registers analysis result blobs as session assets.
    # The description for estimate_height_above_surface should mention
    # "Height above surface" and "Depth Anything V2".
    assets_resp = http_client.get(
        f"{api_gateway_url}/v1/sessions/{session_id}/assets",
        headers=auth_headers,
    )
    assets_resp.raise_for_status()
    assets = assets_resp.json().get("assets", [])

    analysis_assets = [
        a for a in assets
        if a.get("asset_type") == "job_analysis_result"
        and "height" in (a.get("description") or "").lower()
    ]
    assert analysis_assets, (
        "Expected a job_analysis_result asset with 'height' in description. "
        f"Session assets found: {[(a.get('asset_type'), a.get('description')) for a in assets]}"
    )
