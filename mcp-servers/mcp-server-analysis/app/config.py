from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Maps short alias -> LiteLLM model string (overridable via env)
DEFAULT_MODEL_ALIASES: dict[str, str] = {
    "claude-vision": "bedrock/us.amazon.nova-2-lite-v1:0",
    "claude-haiku": "anthropic/claude-haiku-4-5-20251001",
    "yolo": "local/yolo",
    "whisper": "local/whisper",
}


class Settings(BaseSettings):
    azure_storage_connection_string: str = ""
    azure_storage_container_name: str = "videos"
    applicationinsights_connection_string: str = ""
    log_level: str = "INFO"
    service_name: str = "mcp-server-analysis"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5433/videoextract"

    # Provider credentials — set whichever matches tool_frontier_model
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region_name: str = "us-east-1"

    # Frontier model for vision tools (LiteLLM model string), e.g.:
    #   anthropic/claude-opus-4-6
    #   openai/gpt-4o
    #   bedrock/us.anthropic.claude-opus-4-5-v1:0
    tool_frontier_model: str = "bedrock/us.amazon.nova-2-lite-v1:0"

    # Model alias overrides — comma-separated alias=model_string pairs, e.g.
    # "claude-vision=openai/gpt-4o,claude-haiku=openai/gpt-4o-mini"
    model_aliases_override: str = ""

    # Per-frame output token budget used to scale max_tokens with batch size.
    # Effective max_tokens per call = min(n_frames * frontier_tokens_per_frame, frontier_max_tokens).
    # 500 tokens/frame handles detailed JSON (detect_objects_vision: bbox + location per object;
    # analyze_scene: description + objects + activities + setting + mood).
    frontier_tokens_per_frame: int = 500

    # Hard ceiling on output tokens per frontier model call.
    # Must not exceed the configured model's own max output limit:
    #   anthropic/claude-haiku-4-5-20251001  → 8192
    #   bedrock/us.amazon.nova-2-lite-v1:0   → 10000
    #   anthropic/claude-opus-4-6            → 32000
    # Set to Nova Lite's actual ceiling (10000). At 500 tokens/frame this covers
    # up to 20 frames per batch.
    frontier_max_tokens: int = 10000

    # RPM limit for tool frontier model calls — requests per minute.
    # Empty or absent = no limit.  Default: 4 (= 1 call per 15 s).
    # Can be overridden at runtime via the app_settings DB table (key: tool_rpm_limit).
    tool_rpm_limit: int | None = 4

    @field_validator("tool_rpm_limit", mode="before")
    @classmethod
    def _parse_tool_rpm(cls, v: object) -> object:
        if v == "" or v is None:
            return None
        try:
            parsed = int(v)
        except (ValueError, TypeError):
            return None
        return parsed if parsed > 0 else None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def get_model_aliases(self) -> dict[str, str]:
        """Return merged alias map: TOOL_FRONTIER_MODEL seeds claude-vision,
        then MODEL_ALIASES_OVERRIDE takes final precedence."""
        aliases = dict(DEFAULT_MODEL_ALIASES)
        # Wire TOOL_FRONTIER_MODEL into the claude-vision alias so that
        # changing the env var actually affects which model analyze_scene and
        # detect_objects_vision call.
        if self.tool_frontier_model:
            aliases["claude-vision"] = self.tool_frontier_model
        if self.model_aliases_override:
            for pair in self.model_aliases_override.split(","):
                pair = pair.strip()
                if "=" in pair:
                    alias, model = pair.split("=", 1)
                    aliases[alias.strip()] = model.strip()
        return aliases


settings = Settings()
