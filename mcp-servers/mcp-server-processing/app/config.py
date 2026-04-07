from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    azure_storage_connection_string: str = ""
    azure_storage_container_name: str = "videos"
    applicationinsights_connection_string: str = ""
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
