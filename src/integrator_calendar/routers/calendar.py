from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from src.integrator_calendar.config import settings
from src.integrator_calendar import auth, calendar_service
from src.integrator_calendar.schemas.calendar import EventCreate

from datetime import datetime, timedelta
import asyncio
import json
from pathlib import Path

router = APIRouter()

# ============================================================
# ЗАЩИТА ОТ ПАРАЛЛЕЛЬНЫХ ГЕНЕРАЦИЙ
# ============================================================
_is_generating = False


@router.get("/generate/status")
async def get_generation_status():
    """Возвращает текущий статус генерации. Используется фронтендом для поллинга."""
    return {"is_generating": _is_generating}


# ============================================================
# ОСТАЛЬНЫЕ ЭНДПОИНТЫ
# ============================================================

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
        f"&state={account_key}"
    )
    return {"auth_url": auth_url}


@router.get("/auth/callback")
async def auth_callback(code: str, state: str):
    await auth.get_tokens_from_code(code, state)
    return {"status": "success", "message": f"Токен для {state} успешно сохранен!"}


@router.get("/events/{date_str}")
async def read_events(date_str: str, account: str = "account_1"):
    cal_id = settings.calendar_id_1 if account == "account_1" else settings.calendar_id_2
    events = await calendar_service.get_events_for_day(cal_id, date_str, account)
    event_titles = [event.get("summary", "Без названия") for event in events]
    return {"date": date_str, "account": account, "count": len(events), "events": event_titles}


@router.post("/events")
async def add_event(event: EventCreate, account: str = "account_1"):
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
        existing = await calendar_service.get_events_for_day(cal_id, date_str, acc_key)
        is_duplicate = any(
            ev.get("summary") == event.summary and ev.get("start", {}).get("dateTime") == event.start_time
            for ev in existing
        )

        if is_duplicate:
            results.append({"account": acc_key, "status": "skipped", "reason": "Дубликат"})
            continue

        try:
            created = await calendar_service.create_event(
                cal_id, event.summary, event.start_time, event.end_time, acc_key
            )
            results.append({"account": acc_key, "status": "created", "id": created.get("id")})
        except Exception as e:
            results.append({"account": acc_key, "status": "error", "detail": str(e)})

    return {"results": results}


@router.delete("/events/{date_str}")
async def clear_day(date_str: str, account: str = "account_1", keep: str = Query("Сон")):
    targets = []
    if account in ("account_1", "all"):
        targets.append((settings.calendar_id_1, "account_1"))
    if account in ("account_2", "all"):
        targets.append((settings.calendar_id_2, "account_2"))

    if not targets:
        raise HTTPException(status_code=400, detail="Неверный аккаунт")

    tasks = [
        calendar_service.delete_events_for_day(cal_id, date_str, acc_key, keep)
        for cal_id, acc_key in targets
    ]
    deleted_counts = await asyncio.gather(*tasks)

    return {
        "status": "success",
        "results": [
            {"account": acc_key, "deleted": count}
            for (_, acc_key), count in zip(targets, deleted_counts)
        ]
    }


@router.post("/generate")
async def generate_schedule(start_date: str, end_date: str, background_tasks: BackgroundTasks):
    """Запускает генерацию в фоне с защитой от параллельного запуска."""
    global _is_generating

    # ⚠️ Между проверкой и установкой флага НЕТ await,
    # поэтому операция атомарна в рамках event loop — race condition невозможен.
    if _is_generating:
        raise HTTPException(
            status_code=409,
            detail="Генерация уже выполняется. Дождитесь завершения или перезапустите контейнер (docker restart integrator_calendar_src).",
        )

    _is_generating = True

    async def _generate_task():
        global _is_generating
        try:
            print(f"🚀 [BG] Запуск генерации с {start_date} по {end_date}")
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")

            current_dt = start_dt
            current_state = "weekend"
            generated_days = 0

            while current_dt <= end_dt:
                date_str = current_dt.strftime("%Y-%m-%d")
                print(f"⚙️ [BG] Обработка дня: {date_str}")

                try:
                    is_work_anchor_1 = await calendar_service.is_workday_by_allday(
                        settings.calendar_id_1, date_str, "account_1"
                    )
                    is_work_anchor_2 = False
                    if settings.calendar_id_2:
                        is_work_anchor_2 = await calendar_service.is_workday_by_allday(
                            settings.calendar_id_2, date_str, "account_2"
                        )

                    is_work_anchor = is_work_anchor_1 or is_work_anchor_2

                    if is_work_anchor:
                        current_state = "workday"
                    else:
                        if current_state == "workday":
                            current_state = "weekend"

                    await calendar_service.delete_events_for_day(
                        settings.calendar_id_1, date_str, "account_1", keep_summary="Сон"
                    )
                    if settings.calendar_id_2:
                        await calendar_service.delete_events_for_day(
                            settings.calendar_id_2, date_str, "account_2", keep_summary="Сон"
                        )

                    await calendar_service.fill_day_by_template(date_str, current_state)
                    generated_days += 1
                    print(f"✅ [BG] День {date_str} обработан как {current_state}")

                except Exception as e:
                    print(f"❌ [BG] ОШИБКА на дне {date_str}: {e}")
                    print("⏳ Ждем 5 секунд и пробуем следующий день...")
                    await asyncio.sleep(5)

                # Готовим состояние для следующего дня
                if current_state == "workday":
                    next_state = "weekend"
                elif current_state == "weekend":
                    next_state = "study_day"
                else:
                    next_state = "weekend"

                current_state = next_state
                current_dt += timedelta(days=1)
                await asyncio.sleep(1.5)

            total_days = (end_dt - start_dt).days + 1
            print(f"🏁 [BG] Генерация завершена! Обработано {generated_days} из {total_days} дней.")

        except Exception as e:
            print(f"💥 [BG] КРИТИЧЕСКАЯ ОШИБКА: {e}")
        finally:
            _is_generating = False

    background_tasks.add_task(_generate_task)
    return {
        "status": "processing",
        "message": "Генерация запущена в фоне. Кнопки разблокируются автоматически после завершения.",
    }


@router.post("/day/{date_str}")
async def set_manual_day(date_str: str, template_name: str):
    await calendar_service.delete_events_for_day(
        settings.calendar_id_1, date_str, "account_1", keep_summary="Сон"
    )
    if settings.calendar_id_2:
        await calendar_service.delete_events_for_day(
            settings.calendar_id_2, date_str, "account_2", keep_summary="Сон"
        )

    try:
        await calendar_service.fill_day_by_template(date_str, template_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "status": "success",
        "message": f"День {date_str} успешно перезаписан по шаблону '{template_name}'",
    }


@router.get("/templates")
async def get_templates():
    template_path = Path("data/templates.json")
    if not template_path.exists():
        raise HTTPException(status_code=404, detail="Файл templates.json не найден")
    with open(template_path, "r", encoding="utf-8") as f:
        return json.load(f)


@router.post("/templates")
async def save_templates(data: dict):
    template_path = Path("data/templates.json")
    with open(template_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return {"status": "success", "message": "Шаблоны успешно обновлены!"}