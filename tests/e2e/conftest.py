"""
E2E test configuration.
Tests run against the local docker-compose stack (docker-compose up -d).
All service URLs come from environment variables — never hardcoded.
"""
import os
import pytest
import httpx

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
    """HTTP client with a generous timeout for full pipeline runs."""
    with httpx.Client(timeout=180.0) as client:
        yield client


@pytest.fixture(scope="session")
def auth_headers() -> dict:
    """Auth headers — LOCAL_DEV_SKIP_AUTH=true means no real JWT is required."""
    return {"Authorization": "Bearer local-dev-skip-auth"}


# ── Test data wipe ───────────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def wipe_test_data() -> None:
    """Delete all test sessions and their blobs before the suite runs."""
    with httpx.Client(timeout=30.0) as client:
        resp = client.delete(
            f"{API_GATEWAY_URL}/v1/admin/wipe-test-data",
            headers={"Authorization": "Bearer local-dev-skip-auth"},
        )
        resp.raise_for_status()


# ── Frontier model availability ──────────────────────────────────────────────

def _resolve_frontier_model() -> str:
    """Read tool_frontier_model from app_settings, fall back to env var."""
    import asyncio
    import asyncpg

    db_url = os.environ.get("DATABASE_URL", "")
    model: str | None = None

    if db_url:
        async def _fetch() -> str | None:
            url = db_url.replace("postgresql+asyncpg://", "postgresql://")
            try:
                conn = await asyncpg.connect(url, timeout=5)
                try:
                    row = await conn.fetchrow(
                        "SELECT value FROM app_settings WHERE key = 'tool_frontier_model'"
                    )
                    return row["value"] if row else None
                finally:
                    await conn.close()
            except Exception:
                return None

        model = asyncio.run(_fetch())

    if model is None:
        model = os.environ.get("TOOL_FRONTIER_MODEL", "anthropic/claude-opus-4-6")

    return model


def _credentials_present_for_model(model: str) -> bool:
    """Return True if the required credentials for the model provider are set."""
    if model.startswith("anthropic/"):
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    if model.startswith("bedrock/"):
        return bool(os.environ.get("AWS_ACCESS_KEY_ID")) and bool(os.environ.get("AWS_SECRET_ACCESS_KEY"))
    if model.startswith("openai/"):
        return bool(os.environ.get("OPENAI_API_KEY"))
    if model.startswith("azure/"):
        return bool(os.environ.get("AZURE_OPENAI_API_KEY"))
    return False


def _provider_reachable(model: str) -> bool:
    """Return True if the provider's API endpoint is reachable via TCP."""
    import socket

    region = os.environ.get("AWS_REGION_NAME", "us-east-1")
    host_map = {
        "anthropic/": "api.anthropic.com",
        "openai/": "api.openai.com",
        "bedrock/": f"bedrock-runtime.{region}.amazonaws.com",
    }
    host = next((h for prefix, h in host_map.items() if model.startswith(prefix)), None)
    if host is None:
        return True  # unknown provider — don't block
    try:
        socket.setdefaulttimeout(5)
        with socket.create_connection((host, 443), timeout=5):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def frontier_model_available() -> bool:
    """True if the active tool_frontier_model's credentials are present AND the
    provider endpoint is reachable.

    Reads tool_frontier_model from app_settings (DB), falls back to the
    TOOL_FRONTIER_MODEL env var. Credentials required depend on the model
    prefix: anthropic/ -> ANTHROPIC_API_KEY, bedrock/ -> AWS creds, etc.
    Tests are skipped (not failed) when connectivity is absent.
    """
    model = _resolve_frontier_model()
    if not _credentials_present_for_model(model):
        return False
    return _provider_reachable(model)
