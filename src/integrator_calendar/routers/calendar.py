from fastapi import APIRouter
from integrator_calendar.config import settings
from integrator_calendar import auth, calendar_service
from integrator_calendar.schemas.calendar import EventCreate

from datetime import datetime, timedelta
import asyncio
from fastapi import APIRouter, HTTPException, Query
import json
from pathlib import Path

router = APIRouter()

@router.get("/ping")
async def ping():
    return {"message": "pong"}

@router.get("/auth/login")
async def login(account_key: str = "account_1"):
    auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={settings.client_id}"
        f"&redirect_uri={settings.redirect_uri}"
        "&response_type=code"
        "&scope=https://www.googleapis.com/auth/calendar"
        "&access_type=offline"
        "&prompt=consent"
        f"&state={account_key}" # Передаем account_key через state
    )
    return {"auth_url": auth_url}

@router.get("/auth/callback")
async def auth_callback(code: str, state: str):
    # state содержит account_key (account_1 или account_2)
    await auth.get_tokens_from_code(code, state)
    return {"status": "success", "message": f"Токен для {state} успешно сохранен!"}

@router.get("/events/{date_str}")
async def read_events(date_str: str, account: str = "account_1"):
    # Выбираем правильный calendar_id в зависимости от аккаунта
    cal_id = settings.calendar_id_1 if account == "account_1" else settings.calendar_id_2
    events = await calendar_service.get_events_for_day(cal_id, date_str, account)
    
    event_titles = [event.get("summary", "Без названия") for event in events]
    return {"date": date_str, "account": account, "count": len(events), "events": event_titles}

@router.post("/events")
async def add_event(event: EventCreate, account: str = "account_1"):
    # Определяем, какие календари будем трогать
    targets = []
    if account in ("account_1", "all"):
        targets.append((settings.calendar_id_1, "account_1"))
    if account in ("account_2", "all"):
        targets.append((settings.calendar_id_2, "account_2"))
        
    if not targets:
        raise HTTPException(status_code=400, detail="Неверный аккаунт")

    date_str = event.start_time.split("T")[0]
    results = []

    for cal_id, acc_key in targets:
        # 1. Проверка на дубликат для каждого календаря
        existing = await calendar_service.get_events_for_day(cal_id, date_str, acc_key)
        is_duplicate = any(
            ev.get("summary") == event.summary and ev.get("start", {}).get("dateTime") == event.start_time 
            for ev in existing
        )
        
        if is_duplicate:
            results.append({"account": acc_key, "status": "skipped", "reason": "Дубликат"})
            continue
            
        # 2. Создание
        try:
            created = await calendar_service.create_event(
                cal_id, event.summary, event.start_time, event.end_time, acc_key
            )
            results.append({"account": acc_key, "status": "created", "id": created.get("id")})
        except Exception as e:
            results.append({"account": acc_key, "status": "error", "detail": str(e)})

    return {"results": results}

@router.delete("/events/{date_str}")
async def clear_day(
    date_str: str, 
    account: str = "account_1", 
    keep: str = Query("Сон")
):
    targets = []
    if account in ("account_1", "all"):
        targets.append((settings.calendar_id_1, "account_1"))
    if account in ("account_2", "all"):
        targets.append((settings.calendar_id_2, "account_2"))

    if not targets:
        raise HTTPException(status_code=400, detail="Неверный аккаунт")

    # Запускаем удаление ПАРАЛЛЕЛЬНО для обоих аккаунтов
    tasks = [
        calendar_service.delete_events_for_day(cal_id, date_str, acc_key, keep)
        for cal_id, acc_key in targets
    ]
    
    # asyncio.gather выполняет все задачи одновременно
    deleted_counts = await asyncio.gather(*tasks)

    return {
        "status": "success", 
        "results": [
            {"account": acc_key, "deleted": count} 
            for (_, acc_key), count in zip(targets, deleted_counts)
        ]
    }


@router.post("/generate")
async def generate_schedule(start_date: str, end_date: str):
    """
    Генерирует расписание на диапазон дат для ОБОИХ аккаунтов.
    """
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    
    current_dt = start_dt
    current_state = "weekend" 
    
    log = []

    while current_dt <= end_dt:
        date_str = current_dt.strftime("%Y-%m-%d")
        
        # 1. Проверяем якорь "Работа" В ОБОИХ аккаунтах
        is_work_anchor_1 = await calendar_service.is_workday_by_allday(
            settings.calendar_id_1, date_str, "account_1"
        )
        
        is_work_anchor_2 = False
        if settings.calendar_id_2:
            is_work_anchor_2 = await calendar_service.is_workday_by_allday(
                settings.calendar_id_2, date_str, "account_2"
            )
            
        # Если якорь найден хотя бы в одном календаре — это рабочий день
        is_work_anchor = is_work_anchor_1 or is_work_anchor_2
        
        if is_work_anchor:
            current_state = "workday"
        else:
            if current_state == "workday":
                current_state = "weekend"
        
        # 2. Очищаем ОБА календаря (оставляем Сон и All-day)
        await calendar_service.delete_events_for_day(
            settings.calendar_id_1, date_str, "account_1", keep_summary="Сон"
        )
        if settings.calendar_id_2:
            await calendar_service.delete_events_for_day(
                settings.calendar_id_2, date_str, "account_2", keep_summary="Сон"
            )
        
        # 3. Заполняем день
        await calendar_service.fill_day_by_template(date_str, current_state)
        
        log.append({"date": date_str, "filled_as": current_state})
        
        # 4. Готовим состояние для СЛЕДУЮЩЕГО дня
        if current_state == "workday":
            next_state = "weekend"
        elif current_state == "weekend":
            next_state = "study_day"
        else:
            next_state = "weekend"
            
        current_state = next_state
        current_dt += timedelta(days=1)

    return {"status": "success", "generated_days": len(log), "details": log}

@router.post("/day/{date_str}")
async def set_manual_day(date_str: str, template_name: str):
    """
    Принудительно меняет расписание на конкретный день.
    template_name: 'workday', 'weekend' или 'study_day'.
    """
    # 1. Очищаем оба календаря на этот день (кроме Сна и All-day)
    await calendar_service.delete_events_for_day(
        settings.calendar_id_1, date_str, "account_1", keep_summary="Сон"
    )
    if settings.calendar_id_2:
        await calendar_service.delete_events_for_day(
            settings.calendar_id_2, date_str, "account_2", keep_summary="Сон"
        )

    # 2. Заполняем новым шаблоном
    try:
        await calendar_service.fill_day_by_template(date_str, template_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    return {
        "status": "success", 
        "message": f"День {date_str} успешно перезаписан по шаблону '{template_name}'"
    }

@router.get("/templates")
async def get_templates():
    """Отдает текущие шаблоны для отображения на фронтенде."""
    template_path = Path("templates.json")
    if not template_path.exists():
        raise HTTPException(status_code=404, detail="Файл templates.json не найден")
    with open(template_path, "r", encoding="utf-8") as f:
        return json.load(f)

@router.post("/templates")
async def save_templates(data: dict):
    """Сохраняет измененные шаблоны в файл."""
    template_path = Path("templates.json")
    with open(template_path, "w", encoding="utf-8") as f:
        # ensure_ascii=False чтобы русские буквы читались, indent=2 для красоты
        json.dump(data, f, ensure_ascii=False, indent=2)
    return {"status": "success", "message": "Шаблоны успешно обновлены!"}