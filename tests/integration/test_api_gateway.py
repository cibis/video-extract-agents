"""Integration tests for the API Gateway service."""
import pytest
import httpx


def test_api_gateway_health(api_gateway_url, http_client):
    response = http_client.get(f"{api_gateway_url}/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "api-gateway"


def test_create_job(api_gateway_url, http_client, auth_headers):
    response = http_client.post(
        f"{api_gateway_url}/v1/jobs",
        json={
            "videoId": "00000000-0000-0000-0000-000000000003",
            "prompt": "extract all jumps",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert "jobId" in data
    assert data["status"] == "queued"
    return data["jobId"]


def test_get_job_not_found(api_gateway_url, http_client, auth_headers):
    response = http_client.get(
        f"{api_gateway_url}/v1/jobs/00000000-0000-0000-0000-000000000000",
        headers=auth_headers,
    )
    assert response.status_code == 404


def test_create_job_missing_fields(api_gateway_url, http_client, auth_headers):
    response = http_client.post(
        f"{api_gateway_url}/v1/jobs",
        json={"prompt": "extract jumps"},  # Missing videoId
        headers=auth_headers,
    )
    assert response.status_code == 400


def test_request_sas_url(api_gateway_url, http_client, auth_headers):
    response = http_client.post(
        f"{api_gateway_url}/v1/videos",
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert "videoId" in data
    assert "sasUrl" in data
