from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    mcp_analysis_url: str = "http://mcp-server-analysis:8100"
    mcp_processing_url: str = "http://mcp-server-processing:8200"
    port: int = 8300
    tool_call_timeout_seconds: float = 36000.0
    catalogue_refresh_interval_seconds: int = 300
    applicationinsights_connection_string: str = ""
    log_level: str = "INFO"
    azure_storage_connection_string: str = ""
    upload_container: str = "videos"
    database_url: str = "postgresql+asyncpg://postgres:postgres@postgresql:5432/videoextract"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
