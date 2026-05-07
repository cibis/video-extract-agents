from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # LLM model — LiteLLM model string, e.g.:
    #   anthropic/claude-haiku-4-5-20251001
    #   anthropic/claude-sonnet-4-6
    #   openai/gpt-4o
    # Must match the value seeded in app_settings (key: agent_model) so that
    # the fallback behaviour is identical to the DB-configured behaviour.
    agent_model: str = "anthropic/claude-haiku-4-5-20251001"

    # RPM limit for agent LLM calls — requests per minute.
    # Empty or absent = no limit.  Default: 2 (= 1 call per 30 s).
    # Can be overridden at runtime via the app_settings DB table (key: agent_rpm_limit).
    agent_rpm_limit: int | None = 2

    # Fraction of a model's context_window_tokens at which automatic context compression fires.
    # Per-model override: set compression_threshold on the model_context_windows row.
    # Global fallback: this env var (CONTEXT_COMPRESSION_THRESHOLD).
    context_compression_threshold: float = 0.2

    @field_validator("agent_rpm_limit", mode="before")
    @classmethod
    def _parse_agent_rpm(cls, v: object) -> object:
        if v == "" or v is None:
            return None
        try:
            parsed = int(v)
        except (ValueError, TypeError):
            return None
        return parsed if parsed > 0 else None

    # Provider credentials — set whichever matches agent_model
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region_name: str = "us-east-1"

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5433/videoextract"
    azure_service_bus_connection_string: str = ""
    azure_storage_connection_string: str = ""
    azure_storage_container_name: str = "videos"
    mcp_analysis_url: str = "http://mcp-server-analysis:8100"
    mcp_processing_url: str = "http://mcp-server-processing:8200"
    applicationinsights_connection_string: str = ""
    log_level: str = "INFO"
    service_name: str = "agent-orchestrator"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
