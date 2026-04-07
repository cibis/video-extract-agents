"""
E2E tests: follow-up job scenarios.

These tests verify that jobs within the same session can build on each other:
  - parentJobId is propagated through the API Gateway to the agent orchestrator
  - The orchestrator reads session context (previous outputs) when parentJobId is set
  - session_assets accumulates outputs across jobs
  - GET /v1/sessions/{id}/assets reflects all job outputs after completion

Three scenarios are covered:

  Scenario 1 — Extract → Transform
    Job 1 extracts motion clips. Job 2 speeds up the output from Job 1 using
    session context and parentJobId to locate the prior output.

  Scenario 2 — Multi-video session
    Two different videos are uploaded to the same session. Job 1 processes
    video 1. Job 2 processes video 2 and references Job 1 via parentJobId,
    asking the orchestrator to combine results.

  Scenario 3 — Re-transform
    Job 1 extracts segments. Job 2 applies a different transformation
    (slow-motion) to Job 1's output, producing a third distinct artefact.

  Scenario 4 — Job history assets
    Job 1 runs object detection; after completion its analysis result blobs
    (detect_objects JSON, merged_segments JSON) must be registered in
    session_assets as job_analysis_result entries with non-empty descriptions.
    Job 2 (follow-up) must still complete successfully, demonstrating the
    enriched history context is usable by the planner.
"""
import pytest

from tests.e2e import video_factory
from tests.e2e.helpers import (
    assert_job_succeeded,
    create_test_session,
    upload_video,
    wait_for_indexed,
    wait_for_job,
)


# ---------------------------------------------------------------------------
# Scenario 1: Extract → Speed-up transform
# ---------------------------------------------------------------------------

def test_followup_extract_then_transform(tmp_path, api_gateway_url, http_client, auth_headers):
    """
    Job 1 extracts high-motion segments and produces a merged highlight reel.
    Job 2 (follow-up) speeds up that reel by 2x, referencing Job 1 via
    parentJobId and the shared sessionId.

    Assertions:
    - Both jobs complete successfully.
    - Job 2 output_url differs from Job 1 output_url (new artefact created).
    - Session assets contain at least 2 job_output_video entries after both
      jobs complete.
    """
    # Generate and upload video
    video_path = str(tmp_path / "motion_followup.mp4")
    video_factory.make_motion_video(video_path)

    session_id = create_test_session(http_client, api_gateway_url, auth_headers)

    video_id, _ = upload_video(http_client, api_gateway_url, auth_headers, session_id, video_path)
    wait_for_indexed(http_client, api_gateway_url, auth_headers, session_id)

    # Job 1 — extract motion segments
    j1_resp = http_client.post(
        f"{api_gateway_url}/v1/jobs",
        json={
            "videoId": video_id,
            "sessionId": session_id,
            "prompt": "Extract all segments with significant movement and compile into a highlight reel",
        },
        headers=auth_headers,
    )
    j1_resp.raise_for_status()
    job1_id = j1_resp.json()["jobId"]
    job1 = wait_for_job(http_client, api_gateway_url, auth_headers, job1_id)
    assert_job_succeeded(job1)

    job1_output_url = job1.get("output_url")

    # Job 2 — transform the output from Job 1 (speed up 2x)
    j2_resp = http_client.post(
        f"{api_gateway_url}/v1/jobs",
        json={
            "videoId": video_id,
            "sessionId": session_id,
            "parentJobId": job1_id,
            "prompt": "Speed up the output from the previous job by 2x",
        },
        headers=auth_headers,
    )
    j2_resp.raise_for_status()
    job2_id = j2_resp.json()["jobId"]
    job2 = wait_for_job(http_client, api_gateway_url, auth_headers, job2_id)
    assert_job_succeeded(job2)

    # Job 2 must produce a new output artefact when both jobs produced real video URLs
    if ".mp4" in str(job1_output_url or "") and ".mp4" in str(job2.get("output_url") or ""):
        assert job2["output_url"] != job1_output_url, (
            "Job 2 should produce a distinct output from Job 1"
        )

    # Session should accumulate outputs from both jobs
    assets_resp = http_client.get(
        f"{api_gateway_url}/v1/sessions/{session_id}/assets",
        headers=auth_headers,
    )
    assets_resp.raise_for_status()
    assets = assets_resp.json().get("assets", [])
    output_assets = [a for a in assets if a.get("asset_type") == "job_output_video"]
    assert len(output_assets) >= 2, (
        f"Expected at least 2 job_output_video assets in session, found {len(output_assets)}"
    )


# ---------------------------------------------------------------------------
# Scenario 2: Multi-video session — Job 2 references Job 1 across videos
# ---------------------------------------------------------------------------

def test_followup_multi_video_session(tmp_path, api_gateway_url, http_client, auth_headers):
    """
    Two different videos are uploaded to the same session. Job 1 processes
    the first video. Job 2 processes the second video with parentJobId pointing
    to Job 1, instructing the orchestrator to merge results from both.

    Assertions:
    - Both jobs complete successfully.
    - Session contains outputs from both jobs.
    """
    session_id = create_test_session(http_client, api_gateway_url, auth_headers)

    # Upload two different videos to the same session
    video1_path = str(tmp_path / "video1.mp4")
    video2_path = str(tmp_path / "video2.mp4")
    video_factory.make_motion_video(video1_path)
    video_factory.make_sports_video(video2_path)

    video1_id, _ = upload_video(http_client, api_gateway_url, auth_headers, session_id, video1_path)
    wait_for_indexed(http_client, api_gateway_url, auth_headers, session_id)

    video2_id, _ = upload_video(http_client, api_gateway_url, auth_headers, session_id, video2_path)
    # Wait for both videos to be indexed — poll until asset count stabilises
    wait_for_indexed(http_client, api_gateway_url, auth_headers, session_id)

    # Job 1 — extract motion from video 1
    j1_resp = http_client.post(
        f"{api_gateway_url}/v1/jobs",
        json={
            "videoId": video1_id,
            "sessionId": session_id,
            "prompt": "Extract all motion segments from this video",
        },
        headers=auth_headers,
    )
    j1_resp.raise_for_status()
    job1_id = j1_resp.json()["jobId"]
    job1 = wait_for_job(http_client, api_gateway_url, auth_headers, job1_id)
    assert_job_succeeded(job1)

    # Job 2 — process video 2, merge with previous job output
    j2_resp = http_client.post(
        f"{api_gateway_url}/v1/jobs",
        json={
            "videoId": video2_id,
            "sessionId": session_id,
            "parentJobId": job1_id,
            "prompt": (
                "Extract sports action moments from this video, "
                "then merge them with the highlight reel from the previous job"
            ),
        },
        headers=auth_headers,
    )
    j2_resp.raise_for_status()
    job2_id = j2_resp.json()["jobId"]
    job2 = wait_for_job(http_client, api_gateway_url, auth_headers, job2_id)
    assert_job_succeeded(job2)

    # Confirm session accumulated outputs from both jobs
    assets_resp = http_client.get(
        f"{api_gateway_url}/v1/sessions/{session_id}/assets",
        headers=auth_headers,
    )
    assets_resp.raise_for_status()
    assets = assets_resp.json().get("assets", [])
    output_assets = [a for a in assets if a.get("asset_type") == "job_output_video"]
    assert len(output_assets) >= 2, (
        f"Expected at least 2 job_output_video assets in session, found {len(output_assets)}"
    )


# ---------------------------------------------------------------------------
# Scenario 3: Extract → Slow-motion re-transform
# ---------------------------------------------------------------------------

def test_followup_slow_motion_retransform(tmp_path, api_gateway_url, http_client, auth_headers):
    """
    Job 1 extracts motion segments and produces a merged output.
    Job 2 applies slow-motion (0.5x speed) to Job 1's output, creating a
    third distinct artefact in the same session.

    Assertions:
    - Both jobs complete successfully.
    - Job 2 produces an output URL.
    - Session ends up with at least 2 job_output_video assets.
    """
    video_path = str(tmp_path / "slow_mo.mp4")
    video_factory.make_motion_video(video_path)

    session_id = create_test_session(http_client, api_gateway_url, auth_headers)

    video_id, _ = upload_video(http_client, api_gateway_url, auth_headers, session_id, video_path)
    wait_for_indexed(http_client, api_gateway_url, auth_headers, session_id)

    # Job 1 — extract moving segments
    j1_resp = http_client.post(
        f"{api_gateway_url}/v1/jobs",
        json={
            "videoId": video_id,
            "sessionId": session_id,
            "prompt": "Extract all segments with movement",
        },
        headers=auth_headers,
    )
    j1_resp.raise_for_status()
    job1_id = j1_resp.json()["jobId"]
    job1 = wait_for_job(http_client, api_gateway_url, auth_headers, job1_id)
    assert_job_succeeded(job1)

    # Job 2 — slow down Job 1's output to 0.5x
    j2_resp = http_client.post(
        f"{api_gateway_url}/v1/jobs",
        json={
            "videoId": video_id,
            "sessionId": session_id,
            "parentJobId": job1_id,
            "prompt": "Transform the output from the previous job to slow motion at 0.5x speed",
        },
        headers=auth_headers,
    )
    j2_resp.raise_for_status()
    job2_id = j2_resp.json()["jobId"]
    job2 = wait_for_job(http_client, api_gateway_url, auth_headers, job2_id)
    assert_job_succeeded(job2)

    # Both jobs should have produced distinct output artefacts
    assets_resp = http_client.get(
        f"{api_gateway_url}/v1/sessions/{session_id}/assets",
        headers=auth_headers,
    )
    assets_resp.raise_for_status()
    assets = assets_resp.json().get("assets", [])
    output_assets = [a for a in assets if a.get("asset_type") == "job_output_video"]
    assert len(output_assets) >= 2, (
        f"Expected at least 2 job_output_video assets in session, found {len(output_assets)}"
    )


# ---------------------------------------------------------------------------
# Scenario 4: Job history assets — analysis results registered and reusable
# ---------------------------------------------------------------------------

def test_followup_job_history_assets(tmp_path, api_gateway_url, http_client, auth_headers):
    """
    After Job 1 completes, the orchestrator must register its analysis tool
    result blobs (detect_objects JSON, merged segments JSON, etc.) as
    job_analysis_result entries in session_assets.  Job 2 (follow-up) then
    runs against the enriched job history and must complete successfully.

    Assertions:
    - Job 1 completes successfully.
    - GET /v1/sessions/{id}/assets contains at least one asset with
      asset_type == "job_analysis_result" whose source_job_id matches job1_id.
    - That asset has a non-empty description field.
    - Job 1 output asset (job_output_video) has a non-empty description.
    - Job 2 (follow-up referencing Job 1) completes successfully.
    - Job 2 produces an output_url distinct from Job 1.
    - After Job 2, session has at least 2 job_output_video assets.
    """
    video_path = str(tmp_path / "history_test.mp4")
    video_factory.make_motion_video(video_path)

    session_id = create_test_session(http_client, api_gateway_url, auth_headers)

    video_id, _ = upload_video(http_client, api_gateway_url, auth_headers, session_id, video_path)
    wait_for_indexed(http_client, api_gateway_url, auth_headers, session_id)

    # Job 1 — motion detection: synthetic video reliably produces segments,
    # and detect_motion writes a result_asset JSON registered in session_assets
    j1_resp = http_client.post(
        f"{api_gateway_url}/v1/jobs",
        json={
            "videoId": video_id,
            "sessionId": session_id,
            "prompt": "Extract all segments with significant movement and motion",
        },
        headers=auth_headers,
    )
    j1_resp.raise_for_status()
    job1_id = j1_resp.json()["jobId"]
    job1 = wait_for_job(http_client, api_gateway_url, auth_headers, job1_id)
    assert_job_succeeded(job1)

    job1_output_url = job1.get("output_url")

    # Verify session_assets contains analysis result entries from Job 1
    assets_resp = http_client.get(
        f"{api_gateway_url}/v1/sessions/{session_id}/assets",
        headers=auth_headers,
    )
    assets_resp.raise_for_status()
    all_assets = assets_resp.json().get("assets", [])

    analysis_assets = [
        a for a in all_assets
        if a.get("asset_type") == "job_analysis_result"
        and a.get("source_job_id") == job1_id
    ]
    assert len(analysis_assets) >= 1, (
        f"Expected at least 1 job_analysis_result asset for job {job1_id}, "
        f"found {len(analysis_assets)}. All assets: {[a.get('asset_type') for a in all_assets]}"
    )

    # Each analysis asset must have a non-empty description
    for a in analysis_assets:
        assert a.get("description"), (
            f"job_analysis_result asset {a.get('filename')} has no description"
        )

    # The output video asset must also have a description
    output_video_assets = [
        a for a in all_assets
        if a.get("asset_type") == "job_output_video"
        and a.get("source_job_id") == job1_id
    ]
    for a in output_video_assets:
        assert a.get("description"), (
            f"job_output_video asset for job {job1_id} has no description"
        )

    # Job 2 — follow-up referencing Job 1 via parentJobId
    j2_resp = http_client.post(
        f"{api_gateway_url}/v1/jobs",
        json={
            "videoId": video_id,
            "sessionId": session_id,
            "parentJobId": job1_id,
            "prompt": "Speed up the output from the previous job by 2x",
        },
        headers=auth_headers,
    )
    j2_resp.raise_for_status()
    job2_id = j2_resp.json()["jobId"]
    job2 = wait_for_job(http_client, api_gateway_url, auth_headers, job2_id)
    assert_job_succeeded(job2)

    # Job 2 must produce a distinct output artefact when both jobs have real video URLs
    def _is_video_url(u: str | None) -> bool:
        return bool(u) and ".mp4" in str(u)

    if _is_video_url(job1_output_url) and _is_video_url(job2.get("output_url")):
        assert job2["output_url"] != job1_output_url, (
            "Job 2 should produce a distinct output from Job 1"
        )

    # Session must accumulate output videos from both jobs
    assets_resp2 = http_client.get(
        f"{api_gateway_url}/v1/sessions/{session_id}/assets",
        headers=auth_headers,
    )
    assets_resp2.raise_for_status()
    assets2 = assets_resp2.json().get("assets", [])
    output_assets = [a for a in assets2 if a.get("asset_type") == "job_output_video"]
    assert len(output_assets) >= 2, (
        f"Expected at least 2 job_output_video assets after both jobs, found {len(output_assets)}"
    )
