"""
Integration test configuration.
All service base URLs come from environment variables — never hardcoded.
"""
import os
import pytest
import httpx


# ── Service URLs ──────────────────────────────────────────────────────────────
API_GATEWAY_URL = os.environ.get("API_GATEWAY_URL", "http://localhost:8000")
MCP_ANALYSIS_URL = os.environ.get("MCP_ANALYSIS_URL", "http://localhost:8100")
MCP_PROCESSING_URL = os.environ.get("MCP_PROCESSING_URL", "http://localhost:8200")
AGENT_ORCHESTRATOR_URL = os.environ.get("AGENT_ORCHESTRATOR_URL", "http://localhost:8001")


@pytest.fixture(scope="session")
def api_gateway_url() -> str:
    return API_GATEWAY_URL


@pytest.fixture(scope="session")
def mcp_analysis_url() -> str:
    return MCP_ANALYSIS_URL


@pytest.fixture(scope="session")
def mcp_processing_url() -> str:
    return MCP_PROCESSING_URL


@pytest.fixture(scope="session")
def agent_orchestrator_url() -> str:
    return AGENT_ORCHESTRATOR_URL


@pytest.fixture(scope="session")
def http_client() -> httpx.Client:
    """Synchronous HTTP client for integration tests."""
    with httpx.Client(timeout=60.0) as client:
        yield client


@pytest.fixture(scope="session")
def auth_headers() -> dict:
    """
    Auth headers for API Gateway.
    LOCAL_DEV_SKIP_AUTH=true means no real JWT is needed in local/integration mode.
    """
    return {"Authorization": "Bearer local-dev-skip-auth"}
