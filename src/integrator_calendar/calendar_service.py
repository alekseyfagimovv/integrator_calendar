import httpx
from integrator_calendar.auth import get_access_token
from datetime import datetime, timedelta

async def get_events_for_day(calendar_id: str, date_str: str, account_key: str) -> list:
    """Получает все события за конкретный день (date_str в формате YYYY-MM-DD)."""
    access_token = await get_access_token(account_key)
    
    time_min = f"{date_str}T00:00:00+03:00"
    time_max = f"{date_str}T23:59:59+03:00"
    
    url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {
        "timeMin": time_min,
        "timeMax": time_max,
        "singleEvents": True,
        "orderBy": "startTime"
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json().get("items", [])
    
    
async def create_event(calendar_id: str, summary: str, start_time: str, end_time: str, account_key: str) -> dict:
    """Создает событие в календаре."""
    access_token = await get_access_token(account_key)
    
    url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
    headers = {"Authorization": f"Bearer {access_token}"}
    
    body = {
        "summary": summary,
        "start": {"dateTime": start_time},
        "end": {"dateTime": end_time},
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=body)
        response.raise_for_status()
        return response.json()
    

async def delete_events_for_day(calendar_id: str, date_str: str, account_key: str, keep_summary: str = "Сон") -> int:
    """Удаляет все события за день, КРОМЕ keep_summary и событий на весь день (All-day)."""
    access_token = await get_access_token(account_key)
    
    events = await get_events_for_day(calendar_id, date_str, account_key)
    
    deleted_count = 0
    url_base = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
    headers = {"Authorization": f"Bearer {access_token}"}
    
    async with httpx.AsyncClient() as client:
        for ev in events:
            # 1. Пропускаем то, что просили сохранить (Сон)
            if ev.get("summary", "").strip() == keep_summary:
                continue
                
            # 2. Пропускаем All-day события (у них есть ключ "date", но нет "dateTime")
            start_info = ev.get("start", {})
            if "date" in start_info and "dateTime" not in start_info:
                continue
            
            # 3. Удаляем всё остальное
            event_id = ev["id"]
            response = await client.delete(f"{url_base}/{event_id}", headers=headers)
            
            if response.status_code in (200, 204):
                deleted_count += 1
                
    return deleted_count


async def fill_day_by_template(date_str: str, template_name: str):
    """Заполняет конкретный день событиями из шаблона, распределяя их по аккаунтам."""
    import json
    from pathlib import Path
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
        ev_account = ev.get("account", "account_1") # Если не указано, кидаем в первый
        
        start_time = f"{date_str}T{ev['start']}:00+03:00"
        
        if ev["end"] < ev["start"]:
            next_day = datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)
            end_date_str = next_day.strftime("%Y-%m-%d")
            end_time = f"{end_date_str}T{ev['end']}:00+03:00"
        else:
            end_time = f"{date_str}T{ev['end']}:00+03:00"

        # Создаем событие в нужном календаре (или в обоих, если указано "all")
        if ev_account in ("account_1", "all"):
            await create_event(settings.calendar_id_1, summary, start_time, end_time, "account_1")
        if ev_account in ("account_2", "all"):
            await create_event(settings.calendar_id_2, summary, start_time, end_time, "account_2")


async def is_workday_by_allday(calendar_id: str, date_str: str, account_key: str) -> bool:
    """Проверяет, есть ли в этот день All-day событие строго 'Работа 9-21'."""
    events = await get_events_for_day(calendar_id, date_str, account_key)
    
    for ev in events:
        # .lower().strip() убирает лишние пробелы по краям и приводит к нижнему регистру
        summary = ev.get("summary", "").lower().strip() 
        start_info = ev.get("start", {})
        
        # Проверяем, что это All-day событие и название строго равно "работа 9-21"
        if "date" in start_info and "dateTime" not in start_info:
            print(f"[DEBUG] {date_str}: найдено All-day событие '{summary}'")
            if summary == "работа 9-21":
                return True
                
    return False