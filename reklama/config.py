"""Настройки проекта: дефолты + переопределение через .env / переменные окружения.

CLI-флаги (--dry-run, --limit, --reset-progress) сюда не входят — они живут в run.py.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

log = logging.getLogger(__name__)

# Каталог проекта (корень репозитория, на уровень выше этого файла).
BASE_DIR = Path(__file__).resolve().parent.parent

# Загружаем .env один раз при импорте модуля.
load_dotenv(BASE_DIR / ".env")


def _get_str(key: str, default: str) -> str:
    val = os.getenv(key)
    return val if val is not None and val != "" else default


def _get_int(key: str, default: int) -> int:
    val = os.getenv(key)
    if val is None or val == "":
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _get_bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None or val == "":
        return default
    return val.strip().lower() in {"1", "true", "yes", "on", "да", "y"}


# --- Учётные данные Telegram (секреты) ---
API_ID: int = _get_int("TELEGRAM_API_ID", 0)
API_HASH: str = _get_str("TELEGRAM_API_HASH", "")
SESSION_NAME: str = _get_str("SESSION_NAME", "reklama")
# Файл сессии Telethon будет создан рядом со скриптом: <SESSION_NAME>.session
SESSION_PATH: str = str(BASE_DIR / SESSION_NAME)

# --- Содержимое рассылки ---
MESSAGE_FILE: str = _get_str("MESSAGE_FILE", "message.txt")
FORCE_DOCUMENT: bool = _get_bool("FORCE_DOCUMENT", False)

# --- Анти-бан: паузы ---
DELAY_MIN_SEC: int = _get_int("DELAY_MIN_SEC", 30)
DELAY_MAX_SEC: int = _get_int("DELAY_MAX_SEC", 90)
BATCH_SIZE: int = max(1, _get_int("BATCH_SIZE", 50))
BATCH_PAUSE_MIN_SEC: int = _get_int("BATCH_PAUSE_MIN_SEC", 300)
BATCH_PAUSE_MAX_SEC: int = _get_int("BATCH_PAUSE_MAX_SEC", 900)

# --- Окно активности (опц.), формат "09:00-21:00". Пусто = без ограничений. ---
ACTIVE_HOURS: str = _get_str("ACTIVE_HOURS", "")

# --- Умная рассылка и обход лимитов ---
MUTATE_MESSAGE: bool = _get_bool("MUTATE_MESSAGE", True)
MAX_SLOWMODE_WAIT_SEC: int = _get_int("MAX_SLOWMODE_WAIT_SEC", 60)
MAX_FLOODWAIT_ATTEMPTS: int = _get_int("MAX_FLOODWAIT_ATTEMPTS", 5)
MAX_FLOODWAIT_SLEEP_SEC: int = _get_int("MAX_FLOODWAIT_SLEEP_SEC", 1800)


@dataclass(frozen=True)
class ActiveWindow:
    """Распарсенное окно активности в минутах от полуночи (включительно)."""

    start_min: int
    end_min: int


def parse_active_hours(raw: str) -> ActiveWindow | None:
    """Разбирает "HH:MM-HH:MM" -> ActiveWindow. None, если строка пустая/некорректна."""
    if not raw or "-" not in raw:
        return None
    left, _, right = raw.partition("-")
    try:
        sh, sm = left.strip().split(":")
        eh, em = right.strip().split(":")
        start = int(sh) * 60 + int(sm)
        end = int(eh) * 60 + int(em)
    except ValueError:
        return None
    if not (0 <= start < 24 * 60 and 0 < end <= 24 * 60):
        return None
    return ActiveWindow(start_min=start, end_min=end)


def resolve_media_path() -> str | None:
    """Путь к медиафайлу:env MEDIA_PATH, иначе первый файл из media/, иначе None."""
    env_path = os.getenv("MEDIA_PATH")
    if env_path:
        p = Path(env_path)
        p = p if p.is_absolute() else BASE_DIR / p
        if p.is_file():
            return str(p)
        log.warning("MEDIA_PATH не является файлом: %s", p)
    media_dir = BASE_DIR / "media"
    if not media_dir.is_dir():
        return None
    for entry in sorted(media_dir.iterdir()):
        if entry.is_file():
            return str(entry)
    return None


def has_credentials() -> bool:
    """True, если API_ID/API_HASH заданы (минимальная проверка перед запуском)."""
    return API_ID != 0 and bool(API_HASH)


# --- Мультаккаунт ---
#
# Основной аккаунт: TELEGRAM_API_ID / TELEGRAM_API_HASH / SESSION_NAME.
# Дополнительные: TELEGRAM_API_ID_N / TELEGRAM_API_HASH_N / SESSION_NAME_N (N >= 2).
# Прогресс и сессия каждого аккаунта хранятся отдельно:
#   прогресс -> progress_<SESSION_NAME>.json, сессия -> <SESSION_NAME>.session


@dataclass(frozen=True)
class Account:
    """Один Telegram-аккаунт: креды + пути к сессии и файлу прогресса."""

    name: str
    api_id: int
    api_hash: str
    session_path: str
    progress_path: str


def _env_get(key: str) -> str | None:
    val = os.getenv(key)
    return val if val and val.strip() else None


def _build_account(name: str, api_id_raw: str, api_hash: str) -> Account | None:
    try:
        api_id = int(api_id_raw)
    except (TypeError, ValueError):
        return None
    if api_id == 0 or not api_hash:
        return None
    safe_name = name or "reklama"
    return Account(
        name=safe_name,
        api_id=api_id,
        api_hash=api_hash,
        session_path=str(BASE_DIR / safe_name),
        progress_path=str(BASE_DIR / f"progress_{safe_name}.json"),
    )


def load_accounts() -> list[Account]:
    """Собирает все настроенные аккаунты из переменных окружения (загруженных из .env).

    Порядок: основной аккаунт (без суффикса) первым, затем _2, _3 ...
    """
    accounts: list[Account] = []
    seen: set[str] = set()

    p_id = _env_get("TELEGRAM_API_ID")
    p_hash = _env_get("TELEGRAM_API_HASH")
    p_name = _env_get("SESSION_NAME") or "reklama"
    if p_id and p_hash:
        acc = _build_account(p_name, p_id, p_hash)
        if acc and acc.name not in seen:
            accounts.append(acc)
            seen.add(acc.name)

    idx = 2
    while True:
        sid = _env_get(f"TELEGRAM_API_ID_{idx}")
        shash = _env_get(f"TELEGRAM_API_HASH_{idx}")
        if not sid and not shash:
            break
        if sid and shash:
            sname = _env_get(f"SESSION_NAME_{idx}") or f"reklama{idx}"
            acc = _build_account(sname, sid, shash)
            if acc and acc.name not in seen:
                accounts.append(acc)
                seen.add(acc.name)
        idx += 1
        if idx > 100:  # защита от зацикливания
            break

    return accounts


def get_account(name: str | None) -> Account | None:
    """Возвращает аккаунт по имени (None — основной/первый)."""
    accounts = load_accounts()
    if not accounts:
        return None
    if name is None:
        return accounts[0]
    for acc in accounts:
        if acc.name == name:
            return acc
    return None
