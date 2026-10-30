import json
from pathlib import Path
import httpx
from integrator_calendar.config import settings

# Теперь файл называется tokens.json
TOKEN_FILE = Path("tokens.json")

async def get_tokens_from_code(code: str, account_key: str) -> dict:
    url = "https://oauth2.googleapis.com/token"
    payload = {
        "code": code,
        "client_id": settings.client_id,
        "client_secret": settings.client_secret,
        "redirect_uri": settings.redirect_uri,
        "grant_type": "authorization_code",
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(url, data=payload)
        response.raise_for_status()
        tokens = response.json()
        
        # Читаем существующие токены, если файл уже есть
        all_tokens = {}
        if TOKEN_FILE.exists():
            with open(TOKEN_FILE, "r") as f:
                all_tokens = json.load(f)
                
        # Сохраняем токен под ключом (например, 'account_1' или 'account_2')
        all_tokens[account_key] = tokens["refresh_token"]
        
        with open(TOKEN_FILE, "w") as f:
            json.dump(all_tokens, f)
            
        return tokens

async def get_access_token(account_key: str) -> str:
    if not TOKEN_FILE.exists():
        raise FileNotFoundError("Файл tokens.json не найден. Сначала авторизуйтесь.")
        
    with open(TOKEN_FILE, "r") as f:
        all_tokens = json.load(f)
        
    if account_key not in all_tokens:
        raise ValueError(f"Нет токена для {account_key}. Сначала авторизуйтесь.")

    refresh_token = all_tokens[account_key]

    url = "https://oauth2.googleapis.com/token"
    payload = {
        "client_id": settings.client_id,
        "client_secret": settings.client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(url, data=payload)
        response.raise_for_status()
        return response.json()["access_token"]