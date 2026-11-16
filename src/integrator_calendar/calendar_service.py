import httpx
import asyncio
from integrator_calendar.auth import get_access_token
from datetime import datetime, timedelta
from pathlib import Path
import json

TIMEOUT = 60.0

# Обертка для запросов с автоматическим повтором (Retry)
async def _make_request(method: str, url: str, **kwargs):
    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.request(method, url, **kwargs)
                # Если Google вернул 429 (Too Many Requests) или 5xx (ошибка сервера)
                if response.status_code in (429, 500, 502, 503, 504):
                    print(f"⚠️ [API] Google вернул {response.status_code}. Ждем...")
                    await asyncio.sleep(3)
                    continue
                response.raise_for_status()
                return response
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
            print(f"⚠️ [API] Сетевая ошибка (попытка {attempt+1}/{max_retries}): {repr(e)}")
            if attempt == max_retries - 1:
                raise e
            await asyncio.sleep(2 * (attempt + 1)) # 2 сек, потом 4 сек
    return None

async def get_events_for_day(calendar_id: str, date_str: str, account_key: str) -> list:
    access_token = await get_access_token(account_key)
    time_min = f"{date_str}T00:00:00+03:00"
    time_max = f"{date_str}T23:59:59+03:00"
    url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"timeMin": time_min, "timeMax": time_max, "singleEvents": True, "orderBy": "startTime"}
    
    response = await _make_request("GET", url, headers=headers, params=params)
    if response:
        try:
            return response.json().get("items", [])
        except Exception:
            return []
    return []
    
async def create_event(calendar_id: str, summary: str, start_time: str, end_time: str, account_key: str) -> dict:
    access_token = await get_access_token(account_key)
    url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
    headers = {"Authorization": f"Bearer {access_token}"}
    body = {"summary": summary, "start": {"dateTime": start_time}, "end": {"dateTime": end_time}}
    
    response = await _make_request("POST", url, headers=headers, json=body)
    if response:
        return response.json()
    return {}

async def delete_events_for_day(calendar_id: str, date_str: str, account_key: str, keep_summary: str = "Сон") -> int:
    access_token = await get_access_token(account_key)
    events = await get_events_for_day(calendar_id, date_str, account_key)
    url_base = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
    headers = {"Authorization": f"Bearer {access_token}"}
    
    deleted_count = 0
    sem = asyncio.Semaphore(5) 
    
    async def delete_single(ev_id):
        async with sem:
            try:
                # Используем враппер для удаления
                resp = await _make_request("DELETE", f"{url_base}/{ev_id}", headers=headers)
                return resp is not None and resp.status_code in (200, 204)
            except Exception as e:
                return False

    tasks = []
    for ev in events:
        if ev.get("summary", "").strip() == keep_summary:
            continue
        start_info = ev.get("start", {})
        if "date" in start_info and "dateTime" not in start_info:
            continue
        tasks.append(delete_single(ev["id"]))
    
    if tasks:
        results = await asyncio.gather(*tasks)
        deleted_count = sum(results)
            
    return deleted_count

async def fill_day_by_template(date_str: str, template_name: str):
    from integrator_calendar.config import settings
    template_path = Path("templates.json")
    if not template_path.exists():
        raise FileNotFoundError("Файл templates.json не найден в корне проекта!")
        
    with open(template_path, "r", encoding="utf-8") as f:
        templates = json.load(f)
        
    if template_name not in templates:
        raise ValueError(f"Шаблон '{template_name}' не найден в templates.json")

    events_to_create = templates[template_name]

    for ev in events_to_create:
        summary = ev["summary"]
        ev_account = ev.get("account", "account_1") 
        start_time = f"{date_str}T{ev['start']}:00+03:00"
        
        if ev["end"] < ev["start"]:
            next_day = datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)
            end_date_str = next_day.strftime("%Y-%m-%d")
            end_time = f"{end_date_str}T{ev['end']}:00+03:00"
        else:
            end_time = f"{date_str}T{ev['end']}:00+03:00"

        try:
            if ev_account in ("account_1", "all"):
                await create_event(settings.calendar_id_1, summary, start_time, end_time, "account_1")
            if ev_account in ("account_2", "all") and settings.calendar_id_2:
                await create_event(settings.calendar_id_2, summary, start_time, end_time, "account_2")
            await asyncio.sleep(0.3) # Пауза между созданием событий
        except Exception as e:
            print(f"❌ Не удалось создать '{summary}' на {date_str}: {e}")

async def is_workday_by_allday(calendar_id: str, date_str: str, account_key: str) -> bool:
    events = await get_events_for_day(calendar_id, date_str, account_key)
    for ev in events:
        summary = ev.get("summary", "").lower().strip() 
        start_info = ev.get("start", {})
        if "date" in start_info and "dateTime" not in start_info:
            if summary == "работа 9-21":
                return True
    return False