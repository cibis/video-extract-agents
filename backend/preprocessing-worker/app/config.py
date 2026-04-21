from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5433/videoextract"
    azure_service_bus_connection_string: str = ""
    azure_storage_connection_string: str = ""
    azure_storage_container_name: str = "videos"
    applicationinsights_connection_string: str = ""
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
