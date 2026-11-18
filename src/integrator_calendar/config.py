# src/integrator_calendar/config.py
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    calendar_id_1: str = "primary"
    calendar_id_2: str = ""
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = "http://localhost:8001/api/auth/callback"

    model_config = SettingsConfigDict(env_file=".env")

settings = Settings()

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

TEMPLATES_FILE = DATA_DIR / "templates.json"
TOKENS_FILE = DATA_DIR / "tokens.json"