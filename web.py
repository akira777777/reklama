"""FastAPI веб-сервер для управления кампаниями рассылки и поиска в Telegram."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

import run
import search

# Импортируем модули проекта
from reklama import auth, config

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
    clients: dict[str, TelegramClient] = {}
    active_account: str | None = None
    phone_state: dict[str, dict[str, str | None]] = {}  # name -> {phone, code_hash}
    campaign_task: asyncio.Task[None] | None = None
    search_task: asyncio.Task[None] | None = None

state = WebState()
media_lock = asyncio.Lock()


def _resolve_active_account() -> config.Account | None:
    """Возвращает активный аккаунт, инициализируя выборку первым при необходимости."""
    accounts = config.load_accounts()
    if not accounts:
        return None
    if state.active_account is None or state.active_account not in {a.name for a in accounts}:
        state.active_account = accounts[0].name
    return config.get_account(state.active_account)


async def _client_for(account: config.Account) -> TelegramClient:
    """Возвращает (и кэширует) подключённого клиента для конкретного аккаунта."""
    cl = state.clients.get(account.name)
    if cl is None:
        cl = auth.get_client(account)
        state.clients[account.name] = cl
    if not cl.is_connected():
        await cl.connect()
    return cl


async def get_active_client() -> TelegramClient:
    """Возвращает подключённого клиента активного аккаунта."""
    acct = _resolve_active_account()
    if acct is None:
        raise HTTPException(status_code=400, detail="Нет настроенных аккаунтов. Добавьте аккаунт во вкладке «Настройки».")
    return await _client_for(acct)

# --- API Модели ---
class PhoneRequest(BaseModel):
    phone: str

class CodeRequest(BaseModel):
    code: str
    password: str | None = None

class AccountAddRequest(BaseModel):
    api_id: int
    api_hash: str
    name: str

class AccountSwitchRequest(BaseModel):
    name: str

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
    accounts = config.load_accounts()

    # Состояние авторизации и инфо по каждому аккаунту.
    accounts_status: list[dict] = []
    for acct in accounts:
        authorized = False
        info: dict | None = None
        try:
            cl = await _client_for(acct)
            authorized = await cl.is_user_authorized()
            if authorized:
                me = await cl.get_me()
                info = {
                    "username": getattr(me, "username", None) or "",
                    "first_name": getattr(me, "first_name", None) or "",
                    "last_name": getattr(me, "last_name", None) or "",
                    "phone": getattr(me, "phone", None) or "",
                    "id": me.id,
                }
        except Exception as e:
            logger.debug("Статус аккаунта %s недоступен: %s", acct.name, e)
        accounts_status.append({
            "name": acct.name,
            "authorized": authorized,
            "account_info": info,
            "code_sent": bool(state.phone_state.get(acct.name, {}).get("code_hash")),
        })

    active = _resolve_active_account()
    active_name = active.name if active else None
    active_entry = next((a for a in accounts_status if a["name"] == active_name), None)

    if not accounts:
        auth_status = "no_credentials"
    elif active_entry and active_entry["authorized"]:
        auth_status = "authorized"
    elif active_entry and active_entry["code_sent"]:
        auth_status = "code_sent"
    else:
        auth_status = "unauthorized"

    campaign_running = state.campaign_task is not None and not state.campaign_task.done()
    campaign_finished = run.engine.state.get("finished", False)
    if campaign_running:
        campaign_status = "paused" if run.engine.state.get("paused") else "running"
    elif campaign_finished:
        campaign_status = "completed"
    else:
        campaign_status = "idle"

    search_running = state.search_task is not None and not state.search_task.done()
    search_finished = search.search_state.get("finished", False)
    if search_running:
        search_status = "running"
    elif search_finished:
        search_status = "completed"
    else:
        search_status = "idle"

    campaign_stats = dict(run.engine.state)
    campaign_stats.pop("finished", None)

    search_stats = dict(search.search_state)
    search_stats.pop("finished", None)

    return {
        "auth": {
            "status": auth_status,
            "account": active_entry["account_info"] if active_entry else None,
            "phone_submitted": state.phone_state.get(active_name, {}).get("phone") if active_name else None,
        },
        "accounts": accounts_status,
        "active_account": active_name,
        "campaign": {
            "status": campaign_status,
            "running": campaign_running,
            "stats": campaign_stats
        },
        "search": {
            "status": search_status,
            "running": search_running,
            "stats": search_stats
        }
    }


# --- Эндпоинты управления аккаунтами ---
@app.get("/api/accounts")
async def list_accounts():
    return {"accounts": [a.name for a in config.load_accounts()], "active": _resolve_active_account().name if _resolve_active_account() else None}


@app.post("/api/accounts/active")
async def set_active_account(req: AccountSwitchRequest):
    if state.campaign_task and not state.campaign_task.done():
        raise HTTPException(status_code=409, detail="Нельзя менять аккаунт во время активной кампании.")
    if state.search_task and not state.search_task.done():
        raise HTTPException(status_code=409, detail="Нельзя менять аккаунт во время активного поиска.")
    acct = config.get_account(req.name)
    if acct is None:
        raise HTTPException(status_code=404, detail=f"Аккаунт «{req.name}» не найден.")
    state.active_account = acct.name
    logger.info("Активный аккаунт переключен на: %s", acct.name)
    return {"ok": True, "active": acct.name}


@app.post("/api/accounts/add")
async def add_account(req: AccountAddRequest):
    # Запрещаем добавлять во время активных задач.
    if (state.campaign_task and not state.campaign_task.done()) or (state.search_task and not state.search_task.done()):
        raise HTTPException(status_code=409, detail="Сначала остановите активные задачи.")

    name = req.name.strip() or f"reklama{len(config.load_accounts()) + 1}"
    existing_names = {a.name for a in config.load_accounts()}
    if name in existing_names:
        raise HTTPException(status_code=400, detail=f"Аккаунт с именем «{name}» уже существует.")

    env_path = config.BASE_DIR / ".env"
    # Индекс нового аккаунта = следующий свободный _N
    idx = 2
    while os.getenv(f"TELEGRAM_API_ID_{idx}") or os.getenv(f"TELEGRAM_API_HASH_{idx}"):
        idx += 1

    append_lines = [
        "",
        f"# Дополнительный аккаунт {idx}",
        f"TELEGRAM_API_ID_{idx}={req.api_id}",
        f"TELEGRAM_API_HASH_{idx}={req.api_hash}",
        f"SESSION_NAME_{idx}={name}",
    ]
    with env_path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(append_lines) + "\n")

    # Перезагружаем конфиг и .env, чтобы новый аккаунт подхватился.
    import importlib
    config.load_dotenv(config.BASE_DIR / ".env", override=True)
    importlib.reload(config)

    logger.info("Добавлен аккаунт «%s» (индекс %d).", name, idx)
    return {"ok": True, "name": name}


@app.post("/api/auth/send-code")
async def send_code(req: PhoneRequest):
    acct = _resolve_active_account()
    if acct is None:
        raise HTTPException(status_code=400, detail="Нет настроенных аккаунтов.")
    try:
        cl = await _client_for(acct)
        res = await cl.send_code_request(req.phone)
        state.phone_state[acct.name] = {"phone": req.phone, "code_hash": res.phone_code_hash}
        logger.info("Код подтверждения отправлен для аккаунта «%s» на номер: %s", acct.name, req.phone)
        return {"ok": True, "message": "Код отправлен успешно"}
    except Exception as e:
        logger.error("Ошибка отправки кода: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/auth/submit-code")
async def submit_code(req: CodeRequest):
    acct = _resolve_active_account()
    if acct is None:
        raise HTTPException(status_code=400, detail="Нет настроенных аккаунтов.")
    pstate = state.phone_state.get(acct.name, {})
    phone = pstate.get("phone")
    code_hash = pstate.get("code_hash")
    if not phone or not code_hash:
        raise HTTPException(status_code=400, detail="Запрос на код не был инициирован.")
    try:
        cl = await _client_for(acct)
        try:
            await cl.sign_in(phone, req.code, phone_code_hash=code_hash)
        except SessionPasswordNeededError:
            if not req.password:
                return {"ok": False, "requires_password": True, "message": "Требуется пароль двухфакторной аутентификации (2FA)."}
            await cl.sign_in(password=req.password)

        # Сброс временного состояния авторизации для этого аккаунта
        state.phone_state.pop(acct.name, None)

        me = await cl.get_me()
        logger.info("Успешный вход в аккаунт «%s» как: %s", acct.name, getattr(me, "username", None) or me.first_name)
        return {"ok": True, "username": getattr(me, "username", None) or me.first_name}
    except Exception as e:
        logger.error("Ошибка авторизации по коду: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/auth/logout")
async def logout():
    acct = _resolve_active_account()
    if acct is None:
        return {"ok": True}
    name = acct.name
    cl = state.clients.get(name)
    try:
        if cl is not None:
            await cl.log_out()
            await cl.disconnect()
    except Exception as e:
        logger.error("Ошибка при выходе: %s", e)
    finally:
        state.clients.pop(name, None)
        state.phone_state.pop(name, None)

        # Удаляем файл сессии, чтобы очистить кэш
        session_file = Path(acct.session_path + ".session")
        if session_file.exists():
            try:
                session_file.unlink()
                logger.info("Файл сессии аккаунта «%s» удалён.", name)
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
        
        if state.campaign_task and not state.campaign_task.done():
            raise HTTPException(
                status_code=409,
                detail="Нельзя менять конфигурацию во время активной кампании. Остановите кампанию сначала.",
            )
        if state.search_task and not state.search_task.done():
            raise HTTPException(
                status_code=409,
                detail="Нельзя менять конфигурацию во время активного поиска. Остановите поиск сначала.",
            )
        
        import importlib
        importlib.reload(config)
        
        logger.info("Конфигурация успешно обновлена.")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Ошибка при обновлении .env: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e

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
        raise HTTPException(status_code=500, detail=str(e)) from e

@app.post("/api/message/media")
async def upload_media_file(file: UploadFile = File(...)):
    if (state.campaign_task and not state.campaign_task.done()) or (state.search_task and not state.search_task.done()):
        raise HTTPException(status_code=409, detail="Нельзя менять медиа во время активной кампании или поиска.")
    
    async with media_lock:
        media_dir = config.BASE_DIR / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            for item in media_dir.iterdir():
                if item.is_file():
                    item.unlink()
                    
            if not file.filename:
                raise HTTPException(status_code=400, detail="Неверное имя файла.")
            file_path = media_dir / file.filename
            with file_path.open("wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
                
            logger.info("Медиафайл загружен: %s", file.filename)
            return {"ok": True, "filename": file.filename}
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Ошибка при сохранении медиафайла: %s", e)
            raise HTTPException(status_code=500, detail=str(e)) from e

@app.delete("/api/message/media")
async def delete_media_file():
    if (state.campaign_task and not state.campaign_task.done()) or (state.search_task and not state.search_task.done()):
        raise HTTPException(status_code=409, detail="Нельзя менять медиа во время активной кампании или поиска.")
    
    async with media_lock:
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
            raise HTTPException(status_code=500, detail=str(e)) from e

# --- Эндпоинты управления рассылкой ---
@app.post("/api/campaign/start")
async def start_campaign(req: CampaignStartRequest):
    if state.campaign_task and not state.campaign_task.done():
        raise HTTPException(status_code=400, detail="Кампания рассылки уже запущена.")
    
    cl = await get_active_client()
    if not await cl.is_user_authorized():
        raise HTTPException(status_code=401, detail="Необходимо авторизоваться.")

    acct = _resolve_active_account()

    # Задача запускается асинхронно в фоне
    async def campaign_runner():
        try:
            logger.info("Запуск кампании рассылки для аккаунта «%s»...", acct.name if acct else "?")
            await run.run(
                client=cl,
                dry_run=req.dry_run,
                limit=req.limit,
                reset_progress=req.reset_progress,
                no_tui=True,
                account=acct,
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
    
    run.engine.stop()
    logger.info("Получен запрос на остановку кампании. Завершаем текущие действия...")
    return {"ok": True}

@app.post("/api/campaign/pause")
async def pause_campaign():
    if not state.campaign_task or state.campaign_task.done():
        return {"ok": False, "message": "Кампания не запущена."}
        
    run.engine.pause()
    logger.info("Рассылка приостановлена.")
    return {"ok": True}

@app.post("/api/campaign/resume")
async def resume_campaign():
    if not state.campaign_task or state.campaign_task.done():
        return {"ok": False, "message": "Кампания не запущена."}
        
    run.engine.resume()
    logger.info("Рассылка возобновлена.")
    return {"ok": True}

@app.post("/api/campaign/skip-delay")
async def skip_delay():
    if not state.campaign_task or state.campaign_task.done():
        return {"ok": False, "message": "Кампания не запущена."}
        
    run.engine.skip_delay()
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
