from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    calendar_id_1: str = "primary"
    calendar_id_2: str = ""
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = "http://localhost:8001/api/auth/callback"

    model_config = SettingsConfigDict(env_file=".env")

settings = Settings()