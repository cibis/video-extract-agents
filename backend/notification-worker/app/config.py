from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5433/videoextract"
    azure_service_bus_connection_string: str = ""
    azure_communication_services_connection_string: str = ""
    notification_mode: str = "stdout"  # stdout | acs
    front_door_endpoint: str = ""
    front_door_secret: str = ""
    sender_email: str = "noreply@video-extract.example.com"
    applicationinsights_connection_string: str = ""
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
