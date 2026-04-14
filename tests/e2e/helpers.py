"""
Shared helper functions for e2e pipeline tests.

All helpers are synchronous and accept an httpx.Client so they compose
cleanly with pytest session-scoped fixtures.
"""
import os
import time
from pathlib import Path

import httpx


# ---------------------------------------------------------------------------
# Session creation
# ---------------------------------------------------------------------------

def create_test_session(
    client: httpx.Client,
    base_url: str,
    auth_headers: dict,
) -> str:
    """
    Create a session tagged as a test run (is_test=True).

    Returns the sessionId string.
    """
    resp = client.post(
        f"{base_url}/v1/sessions",
        json={"isTest": True},
        headers=auth_headers,
    )
    resp.raise_for_status()
    return resp.json()["sessionId"]


# ---------------------------------------------------------------------------
# Video upload
# ---------------------------------------------------------------------------

def upload_video(
    client: httpx.Client,
    base_url: str,
    auth_headers: dict,
    session_id: str,
    video_path: str,
) -> tuple[str, str]:
    """
    Register a video with the API Gateway and upload it to blob storage.

    Returns (video_id, blob_url).

    Flow:
      POST /v1/videos {sessionId, filename}  →  {videoId, uploadUrl, blobPath}
      PUT  uploadUrl  with raw video bytes
    """
    filename = Path(video_path).name

    # 1. Request an upload URL
    resp = client.post(
        f"{base_url}/v1/videos",
        json={"sessionId": session_id, "filename": filename},
        headers=auth_headers,
    )
    resp.raise_for_status()
    data = resp.json()
    video_id: str = data["videoId"]
    upload_url: str = data["uploadUrl"]
    blob_path: str = data["blobPath"]

    # 2. Upload raw bytes to the blob-proxy (local) or SAS URL (CI/prod)
    # When running inside Docker the api-gateway returns uploadUrl with
    # http://localhost:8000 (its BLOB_PROXY_BASE_URL default), but localhost
    # inside the test-runner container is not the api-gateway. Rewrite the
    # origin to match the api-gateway's Docker-network address.
    from urllib.parse import urlparse
    gw = urlparse(base_url)
    up = urlparse(upload_url)
    if gw.hostname != "localhost" and up.hostname == "localhost":
        upload_url = upload_url.replace(
            f"{up.scheme}://{up.netloc}",
            f"{gw.scheme}://{gw.netloc}",
        )

    video_bytes = Path(video_path).read_bytes()
    put_resp = client.put(
        upload_url,
        content=video_bytes,
        headers={"Content-Type": "video/mp4", "x-ms-blob-type": "BlockBlob"},
    )
    put_resp.raise_for_status()

    # Derive the internal blob URL (Azurite format used by backend services)
    container = os.environ.get("AZURE_STORAGE_CONTAINER_NAME", "videos")
    blob_url = f"http://azurite:10000/devstoreaccount1/{container}/{blob_path}"

    return video_id, blob_url


# ---------------------------------------------------------------------------
# Wait for preprocessing (VIDEO_INDEXED)
# ---------------------------------------------------------------------------

def wait_for_indexed(
    client: httpx.Client,
    base_url: str,
    auth_headers: dict,
    session_id: str,
    timeout: int = 36000,
) -> None:
    """
    Block until the preprocessing worker has indexed the uploaded video.

    Strategy: poll GET /v1/sessions/{id}/assets and wait until any asset has
    a non-null label. The api-gateway creates the initial uploaded_video row
    with label=None. The preprocessing worker upserts the same row setting
    label='video:{video_id}' — the label appearing signals indexing is done.

    Raises TimeoutError if the video is not indexed within `timeout` seconds.
    """
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        resp = client.get(
            f"{base_url}/v1/sessions/{session_id}/assets",
            headers=auth_headers,
        )
        resp.raise_for_status()
        assets = resp.json().get("assets", [])

        # Preprocessing worker sets label='video:{id}'; api-gateway leaves it None
        if any(a.get("label") for a in assets):
            return

        time.sleep(3)

    raise TimeoutError(
        f"Video in session {session_id} was not indexed within {timeout}s. "
        "Check preprocessing-worker logs: "
        "docker logs docker-compose-preprocessing-worker-1"
    )


# ---------------------------------------------------------------------------
# Wait for job completion
# ---------------------------------------------------------------------------

def wait_for_job(
    client: httpx.Client,
    base_url: str,
    auth_headers: dict,
    job_id: str,
    timeout: int = 36000,
) -> dict:
    """
    Poll GET /v1/jobs/{job_id} until status is 'completed' or 'failed'.

    Returns the full job dict on completion.
    Raises TimeoutError if the job does not finish within `timeout` seconds.
    """
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        resp = client.get(
            f"{base_url}/v1/jobs/{job_id}",
            headers=auth_headers,
        )
        resp.raise_for_status()
        job = resp.json()
        status = job.get("status")

        if status in ("completed", "failed"):
            return job

        time.sleep(4)

    raise TimeoutError(
        f"Job {job_id} did not complete within {timeout}s. "
        "Check agent-orchestrator logs: "
        "docker logs docker-compose-agent-orchestrator-1"
    )


# ---------------------------------------------------------------------------
# Job submission
# ---------------------------------------------------------------------------

def submit_job(
    client: httpx.Client,
    base_url: str,
    auth_headers: dict,
    video_id: str,
    session_id: str,
    prompt: str,
    parent_job_id: str | None = None,
    test_name: str | None = None,
) -> dict:
    """
    POST /v1/jobs and return the response dict.

    If test_name is provided, registers the job ID with the session-level
    job registry so collect_e2e_logs can fetch per-test action logs.
    """
    from tests.e2e.conftest import register_job
    body: dict = {"videoId": video_id, "sessionId": session_id, "prompt": prompt}
    if parent_job_id:
        body["parentJobId"] = parent_job_id
    resp = client.post(f"{base_url}/v1/jobs", json=body, headers=auth_headers)
    resp.raise_for_status()
    job = resp.json()
    if test_name:
        register_job(test_name, job["jobId"])
    return job


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------

def assert_job_succeeded(job: dict) -> None:
    """
    Assert that a job completed successfully.

    A job is considered successful if:
      - status == 'completed' with a non-null output_url (real video produced), OR
      - status == 'completed' with output_url == null (pipeline ran cleanly but
        found no matching content — no_matching_segments outcome).

    Fails hard if status == 'failed' (indicates a crash, not just empty results).
    """
    status = job.get("status")
    assert status != "failed", (
        f"Job {job.get('id')} failed with error: {job.get('error')}"
    )
    assert status == "completed", (
        f"Job {job.get('id')} has unexpected status: {status}"
    )


def assert_tool_invoked(
    client: httpx.Client,
    base_url: str,
    auth_headers: dict,
    job_id: str,
    tool_name: str,
) -> None:
    """
    Assert that a specific MCP tool was invoked during the job by checking
    the job_logs endpoint.
    """
    resp = client.get(
        f"{base_url}/v1/jobs/{job_id}/logs",
        headers=auth_headers,
    )
    resp.raise_for_status()
    logs = resp.json().get("logs", [])
    tool_names = [log.get("tool_name") for log in logs if log.get("tool_name")]
    assert tool_name in tool_names, (
        f"Expected tool '{tool_name}' to appear in job logs for job {job_id}. "
        f"Tools invoked: {tool_names}"
    )
