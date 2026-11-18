import json
import httpx
import asyncio

from pathlib import Path
from datetime import datetime, timedelta
from src.integrator_calendar.config import settings
from src.integrator_calendar.config import TOKENS_FILE


_token_cache = {}

async def get_tokens_from_code(code: str, account_key: str) -> dict:
    url = "https://oauth2.googleapis.com/token"
    payload = {
        "code": code,
        "client_id": settings.client_id,
        "client_secret": settings.client_secret,
        "redirect_uri": settings.redirect_uri,
        "grant_type": "authorization_code",
    }
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, data=payload)
                response.raise_for_status()
                tokens = response.json()
                break
        except (httpx.ConnectError, httpx.ReadTimeout) as e:
            print(f"⚠️ [AUTH] Сетевая ошибка (попытка {attempt+1}/{max_retries}): {repr(e)}")
            if attempt == max_retries - 1:
                raise Exception(f"Не удалось авторизоваться после {max_retries} попыток: {str(e)}")
            await asyncio.sleep(2)
        except Exception as e:
            raise Exception(f"Ошибка авторизации Google: {response.text}")

    all_tokens = {}
    if TOKENS_FILE.exists():
        with open(TOKENS_FILE, "r") as f:
            all_tokens = json.load(f)
            
    all_tokens[account_key] = tokens["refresh_token"]
    
    with open(TOKENS_FILE, "w") as f:
        json.dump(all_tokens, f)
        
    return tokens

async def get_access_token(account_key: str) -> str:
    now = datetime.now()
    
    if account_key in _token_cache:
        cached_data = _token_cache[account_key]
        if cached_data["expires_at"] > now + timedelta(minutes=2):
            return cached_data["token"]

    if not TOKENS_FILE.exists():
        raise FileNotFoundError("Файл tokens.json не найден. Сначала авторизуйтесь.")
        
    with open(TOKENS_FILE, "r") as f:
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
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, data=payload)
                if response.status_code != 200:
                    raise Exception(f"Google API вернул {response.status_code}: {response.text}")
                
                data = response.json()
                new_token = data["access_token"]
                expires_in = data.get("expires_in", 3600)
                
                _token_cache[account_key] = {
                    "token": new_token,
                    "expires_at": now + timedelta(seconds=expires_in)
                }
                return new_token
        except (httpx.ConnectError, httpx.ReadTimeout) as e:
            print(f"⚠️ [AUTH] Ошибка сети при получении токена (попытка {attempt+1}/{max_retries}): {repr(e)}")
            if attempt == max_retries - 1:
                raise Exception(f"Не удалось обновить токен: {str(e)}")
            await asyncio.sleep(2)