from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from integrator_calendar.routers import calendar

app = FastAPI(title="Calendar Automator")

# Настраиваем путь к папке с HTML-шаблонами
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Подключаем API
app.include_router(calendar.router, prefix="/api", tags=["Calendar"])

# Отдаем HTML-страницу на главном маршруте
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")