"""Integration test: job creation and status flow via API Gateway."""
import time
import pytest
import httpx


def test_job_lifecycle(api_gateway_url, http_client, auth_headers):
    """Create a job and verify it can be retrieved."""
    # Create a job
    create_resp = http_client.post(
        f"{api_gateway_url}/v1/jobs",
        json={
            "videoId": "00000000-0000-0000-0000-000000000003",
            "prompt": "Extract all exciting moments from this video",
        },
        headers=auth_headers,
    )
    assert create_resp.status_code == 201
    job_id = create_resp.json()["jobId"]

    # Retrieve the job
    get_resp = http_client.get(
        f"{api_gateway_url}/v1/jobs/{job_id}",
        headers=auth_headers,
    )
    assert get_resp.status_code == 200
    job = get_resp.json()
    assert job["id"] == job_id
    assert job["status"] == "queued"


def test_output_not_available_before_completion(api_gateway_url, http_client, auth_headers):
    """Verify output endpoint returns 404 when job not completed."""
    create_resp = http_client.post(
        f"{api_gateway_url}/v1/jobs",
        json={
            "videoId": "00000000-0000-0000-0000-000000000003",
            "prompt": "test",
        },
        headers=auth_headers,
    )
    assert create_resp.status_code == 201
    job_id = create_resp.json()["jobId"]

    output_resp = http_client.get(
        f"{api_gateway_url}/v1/outputs/{job_id}",
        headers=auth_headers,
    )
    # Job is queued/processing — output not available yet
    assert output_resp.status_code == 404


def test_agent_orchestrator_health(agent_orchestrator_url, http_client):
    response = http_client.get(f"{agent_orchestrator_url}/health")
    assert response.status_code == 200
    assert response.json()["service"] == "agent-orchestrator"
