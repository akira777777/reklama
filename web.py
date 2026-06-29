"""FastAPI веб-сервер для управления кампаниями рассылки и поиска в Telegram."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Импортируем модули проекта
from reklama import auth, config, progress
import run
import search
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

# Настройка логирования для веб-сервера
logger = logging.getLogger("web")

app = FastAPI(title="reklama Web Interface")

# --- Слой перехвата логов ---
class InMemoryLogHandler(logging.Handler):
    """Хендлер для сохранения последних логов в памяти веб-сервера."""
    def __init__(self, max_records: int = 150):
        super().__init__()
        self.records: list[dict[str, str]] = []
        self.max_records = max_records

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            from reklama.utils import clean_control_chars
            msg_clean = clean_control_chars(msg)
            
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.records.append({
                "timestamp": ts,
                "level": record.levelname,
                "name": record.name,
                "message": msg_clean
            })
            if len(self.records) > self.max_records:
                self.records.pop(0)
        except Exception:
            self.handleError(record)

# Инициализируем и добавляем хендлер к корневому логгеру
in_memory_log_handler = InMemoryLogHandler()
in_memory_log_handler.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger().addHandler(in_memory_log_handler)

# --- Глобальное состояние веб-сервера ---
class WebState:
    client: TelegramClient | None = None
    phone_number: str | None = None
    phone_code_hash: str | None = None
    campaign_task: asyncio.Task[None] | None = None
    search_task: asyncio.Task[None] | None = None

state = WebState()

async def get_active_client() -> TelegramClient:
    """Возвращает или инициализирует активный TelegramClient."""
    if state.client is None:
        state.client = auth.get_client()
    if not state.client.is_connected():
        await state.client.connect()
    return state.client

# --- API Модели ---
class PhoneRequest(BaseModel):
    phone: str

class CodeRequest(BaseModel):
    code: str
    password: str | None = None

class ConfigUpdateRequest(BaseModel):
    settings: dict[str, str]

class MessageUpdateRequest(BaseModel):
    text: str

class CampaignStartRequest(BaseModel):
    dry_run: bool = False
    limit: int | None = None
    reset_progress: bool = False

class SearchStartRequest(BaseModel):
    query: str | None = None
    links: list[str] | None = None
    join: bool = False
    limit: int = 20
    delay_min: int = 15
    delay_max: int = 30
    join_batch_size: int = 5
    join_batch_delay_min: int = 180
    join_batch_delay_max: int = 360

# --- Настройка путей для статических файлов ---
BASE_WEB_DIR = Path(__file__).resolve().parent / "reklama" / "web"
TEMPLATES_DIR = BASE_WEB_DIR / "templates"
STATIC_DIR = BASE_WEB_DIR / "static"

TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

# Монтируем статические файлы
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# --- Эндпоинты интерфейса ---
@app.get("/", response_class=HTMLResponse)
async def read_index():
    index_file = TEMPLATES_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="index.html not found. Please wait until UI files are generated.")
    return index_file.read_text(encoding="utf-8")

# --- Эндпоинты авторизации ---
@app.get("/api/status")
async def get_status():
    auth_status = "unauthorized"
    account_info = None
    
    if config.has_credentials():
        try:
            cl = await get_active_client()
            is_auth = await cl.is_user_authorized()
            if is_auth:
                auth_status = "authorized"
                me = await cl.get_me()
                account_info = {
                    "username": getattr(me, "username", None) or "",
                    "first_name": getattr(me, "first_name", None) or "",
                    "last_name": getattr(me, "last_name", None) or "",
                    "phone": getattr(me, "phone", None) or "",
                    "id": me.id
                }
            elif state.phone_code_hash:
                auth_status = "code_sent"
        except Exception as e:
            logger.error("Ошибка при проверке статуса Telethon: %s", e)
            auth_status = "unauthorized"
    else:
        auth_status = "no_credentials"

    # Сбор статуса рассылки
    campaign_status = "idle"
    if state.campaign_task and not state.campaign_task.done():
        campaign_status = "paused" if run.control_state.get("paused") else "running"
    elif run.control_state.get("state") == "Завершено":
        campaign_status = "completed"

    # Сбор статуса поиска
    search_status = "idle"
    if state.search_task and not state.search_task.done():
        search_status = "running"
    elif search.search_state.get("status") == "Завершено":
        search_status = "completed"

    return {
        "auth": {
            "status": auth_status,
            "account": account_info,
            "phone_submitted": state.phone_number
        },
        "campaign": {
            "status": campaign_status,
            "running": state.campaign_task is not None and not state.campaign_task.done(),
            "stats": run.control_state
        },
        "search": {
            "status": search_status,
            "running": state.search_task is not None and not state.search_task.done(),
            "stats": search.search_state
        }
    }

@app.post("/api/auth/send-code")
async def send_code(req: PhoneRequest):
    if not config.has_credentials():
        raise HTTPException(status_code=400, detail="Сначала укажите API_ID и API_HASH в настройках.")
    try:
        cl = await get_active_client()
        res = await cl.send_code_request(req.phone)
        state.phone_code_hash = res.phone_code_hash
        state.phone_number = req.phone
        logger.info("Код подтверждения отправлен на номер: %s", req.phone)
        return {"ok": True, "message": "Код отправлен успешно"}
    except Exception as e:
        logger.error("Ошибка отправки кода: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/auth/submit-code")
async def submit_code(req: CodeRequest):
    if not state.phone_number or not state.phone_code_hash:
        raise HTTPException(status_code=400, detail="Запрос на код не был инициирован.")
    try:
        cl = await get_active_client()
        try:
            await cl.sign_in(state.phone_number, req.code, phone_code_hash=state.phone_code_hash)
        except SessionPasswordNeededError:
            if not req.password:
                return {"ok": False, "requires_password": True, "message": "Требуется пароль двухфакторной аутентификации (2FA)."}
            await cl.sign_in(password=req.password)
        
        # Сброс временного состояния авторизации
        state.phone_code_hash = None
        state.phone_number = None
        
        me = await cl.get_me()
        logger.info("Успешный вход в систему как: %s", getattr(me, "username", None) or me.first_name)
        return {"ok": True, "username": getattr(me, "username", None) or me.first_name}
    except Exception as e:
        logger.error("Ошибка авторизации по коду: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/auth/logout")
async def logout():
    try:
        cl = await get_active_client()
        await cl.log_out()
        await cl.disconnect()
    except Exception as e:
        logger.error("Ошибка при выходе: %s", e)
    finally:
        state.client = None
        state.phone_code_hash = None
        state.phone_number = None
        
        # Удаляем файл сессии, чтобы очистить кэш
        session_file = Path(config.SESSION_PATH + ".session")
        if session_file.exists():
            try:
                session_file.unlink()
                logger.info("Файл сессии удален.")
            except Exception as e:
                logger.warning("Не удалось удалить файл сессии: %s", e)
                
    return {"ok": True}

# --- Эндпоинты настроек (.env) ---
@app.get("/api/config")
async def get_config():
    env_path = config.BASE_DIR / ".env"
    env_content = {}
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env_content[k.strip()] = v.strip()
        except Exception as e:
            logger.error("Ошибка чтения .env: %s", e)
    
    # Дозаполняем дефолтами, если не заданы
    for k in ["TELEGRAM_API_ID", "TELEGRAM_API_HASH", "DELAY_MIN_SEC", "DELAY_MAX_SEC", 
              "BATCH_SIZE", "BATCH_PAUSE_MIN_SEC", "BATCH_PAUSE_MAX_SEC", "ACTIVE_HOURS"]:
        if k not in env_content:
            env_content[k] = ""
    return env_content

@app.post("/api/config")
async def update_config(req: ConfigUpdateRequest):
    env_path = config.BASE_DIR / ".env"
    try:
        lines = []
        existing_keys = set()
        
        # Сначала читаем существующий .env, чтобы сохранить комментарии
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.strip() and not line.strip().startswith("#") and "=" in line:
                    k, _ = line.split("=", 1)
                    k = k.strip()
                    if k in req.settings:
                        lines.append(f"{k}={req.settings[k]}")
                        existing_keys.add(k)
                    else:
                        lines.append(line)
                else:
                    lines.append(line)
                    
        # Добавляем новые ключи, которых не было
        for k, v in req.settings.items():
            if k not in existing_keys:
                lines.append(f"{k}={v}")
                
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        
        # Перезагружаем настройки в config-модуле
        import importlib
        importlib.reload(config)
        
        logger.info("Конфигурация успешно обновлена.")
        return {"ok": True}
    except Exception as e:
        logger.error("Ошибка при обновлении .env: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

# --- Эндпоинты управления сообщением и медиа ---
@app.get("/api/message")
async def get_message_content():
    msg_path = config.BASE_DIR / config.MESSAGE_FILE
    text = ""
    if msg_path.exists():
        text = msg_path.read_text(encoding="utf-8")
        
    media_file = config.resolve_media_path()
    media_name = Path(media_file).name if media_file else None
    
    return {"text": text, "media": media_name}

@app.post("/api/message")
async def update_message_content(req: MessageUpdateRequest):
    msg_path = config.BASE_DIR / config.MESSAGE_FILE
    try:
        msg_path.write_text(req.text, encoding="utf-8")
        logger.info("Текст рассылки обновлен.")
        return {"ok": True}
    except Exception as e:
        logger.error("Ошибка сохранения сообщения: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/message/media")
async def upload_media_file(file: UploadFile = File(...)):
    media_dir = config.BASE_DIR / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # Очищаем старые файлы в media/, так как скрипт выбирает первый попавшийся
        for item in media_dir.iterdir():
            if item.is_file():
                item.unlink()
                
        file_path = media_dir / file.filename
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        logger.info("Медиафайл загружен: %s", file.filename)
        return {"ok": True, "filename": file.filename}
    except Exception as e:
        logger.error("Ошибка при сохранении медиафайла: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/message/media")
async def delete_media_file():
    media_dir = config.BASE_DIR / "media"
    try:
        if media_dir.exists():
            for item in media_dir.iterdir():
                if item.is_file():
                    item.unlink()
        logger.info("Медиафайлы удалены.")
        return {"ok": True}
    except Exception as e:
        logger.error("Ошибка удаления медиафайлов: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

# --- Эндпоинты управления рассылкой ---
@app.post("/api/campaign/start")
async def start_campaign(req: CampaignStartRequest):
    if state.campaign_task and not state.campaign_task.done():
        raise HTTPException(status_code=400, detail="Кампания рассылки уже запущена.")
    
    cl = await get_active_client()
    if not await cl.is_user_authorized():
        raise HTTPException(status_code=401, detail="Необходимо авторизоваться.")

    # Задача запускается асинхронно в фоне
    async def campaign_runner():
        try:
            logger.info("Запуск кампании рассылки...")
            await run.run(
                client=cl,
                dry_run=req.dry_run,
                limit=req.limit,
                reset_progress=req.reset_progress,
                no_tui=True
            )
        except Exception as e:
            logger.error("Ошибка во время выполнения кампании рассылки: %s", e, exc_info=True)
        finally:
            logger.info("Кампания рассылки завершена.")

    state.campaign_task = asyncio.create_task(campaign_runner())
    return {"ok": True, "message": "Кампания запущена в фоновом режиме"}

@app.post("/api/campaign/stop")
async def stop_campaign():
    if not state.campaign_task or state.campaign_task.done():
        return {"ok": False, "message": "Кампания не запущена."}
    
    run.control_state["running"] = False
    logger.info("Получен запрос на остановку кампании. Завершаем текущие действия...")
    return {"ok": True}

@app.post("/api/campaign/pause")
async def pause_campaign():
    if not state.campaign_task or state.campaign_task.done():
        return {"ok": False, "message": "Кампания не запущена."}
        
    run.control_state["paused"] = True
    logger.info("Рассылка приостановлена.")
    return {"ok": True}

@app.post("/api/campaign/resume")
async def resume_campaign():
    if not state.campaign_task or state.campaign_task.done():
        return {"ok": False, "message": "Кампания не запущена."}
        
    run.control_state["paused"] = False
    logger.info("Рассылка возобновлена.")
    return {"ok": True}

@app.post("/api/campaign/skip-delay")
async def skip_delay():
    if not state.campaign_task or state.campaign_task.done():
        return {"ok": False, "message": "Кампания не запущена."}
        
    run.control_state["skip_delay"] = True
    logger.info("Пропуск текущего ожидания/задержки.")
    return {"ok": True}

# --- Эндпоинты поиска групп ---
@app.post("/api/search/start")
async def start_search(req: SearchStartRequest):
    if state.search_task and not state.search_task.done():
        raise HTTPException(status_code=400, detail="Процесс поиска уже запущен.")
        
    cl = await get_active_client()
    if not await cl.is_user_authorized():
        raise HTTPException(status_code=401, detail="Необходимо авторизоваться.")

    # Собираем MockArgs
    args = search.SearchArgs(
        query=req.query,
        links=req.links,
        join=req.join,
        limit=req.limit,
        delay_min=req.delay_min,
        delay_max=req.delay_max,
        join_batch_size=req.join_batch_size,
        join_batch_delay_min=req.join_batch_delay_min,
        join_batch_delay_max=req.join_batch_delay_max
    )

    async def search_runner():
        try:
            logger.info("Запуск поиска групп...")
            await search._run_search(cl, args)
        except Exception as e:
            logger.error("Ошибка во время поиска групп: %s", e)
        finally:
            logger.info("Процесс поиска групп завершен.")

    state.search_task = asyncio.create_task(search_runner())
    return {"ok": True, "message": "Поиск групп запущен"}

@app.post("/api/search/stop")
async def stop_search():
    if not state.search_task or state.search_task.done():
        return {"ok": False, "message": "Поиск не запущен."}
        
    search.search_state["running"] = False
    logger.info("Получен запрос на остановку поиска.")
    return {"ok": True}

# --- Эндпоинт получения логов ---
@app.get("/api/logs")
async def get_logs():
    return in_memory_log_handler.records

# --- Запуск веб-сервера ---
if __name__ == "__main__":
    uvicorn.run("web:app", host="0.0.0.0", port=8000, reload=False)
