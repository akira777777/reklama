"""Чтение/запись progress.json и resume. Чистые преобразования отделены от I/O.

Структура файла:
    {"<chat_id>": {"status": "sent|skipped|error", "reason": "...", "ts": 123.0}, ...}
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from . import config

log = logging.getLogger(__name__)

DEFAULT_PATH: Path = config.BASE_DIR / "progress.json"

# Статусы отправки.
STATUS_SENT = "sent"
STATUS_SKIPPED = "skipped"
STATUS_ERROR = "error"


# --- Чистые преобразования (тестируются без файловой системы) ---


def should_skip_state(state: dict[str, Any], chat_id: int) -> bool:
    """True, если чат уже успешно отправлен (для resume)."""
    entry = state.get(str(chat_id))
    if not isinstance(entry, dict):
        return False
    return entry.get("status") == STATUS_SENT


def summarize(state: dict[str, Any]) -> dict[str, int]:
    """Сводка по статусам: {sent, skipped, error, total}."""
    totals = {STATUS_SENT: 0, STATUS_SKIPPED: 0, STATUS_ERROR: 0}
    for entry in state.values():
        if not isinstance(entry, dict):
            continue
        status = entry.get("status")
        if status in totals:
            totals[status] += 1
    totals["total"] = len(state)
    return totals


def report_from(state: dict[str, Any]) -> str:
    """Человекочитаемый отчёт по сводке."""
    s = summarize(state)
    return (
        f"Итого: {s['total']} | отправлено: {s['sent']} | "
        f"пропущено: {s['skipped']} | ошибок: {s['error']}"
    )


# --- I/O слой ---


def _load_raw(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        log.warning("progress.json повреждён или недоступен — стартуем с чистого листа.")
        return {}


def _save_raw(state: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True)


def load(path: Path | str | None = None) -> dict[str, Any]:
    """Загружает состояние прогресса из файла."""
    return _load_raw(Path(path) if path else DEFAULT_PATH)


def apply(state: dict[str, Any], chat_id: int, status: str, reason: str = "") -> None:
    """Мутирует состояние в памяти: добавляет/обновляет запись чата (без I/O)."""
    state[str(chat_id)] = {"status": status, "reason": reason, "ts": time.time()}


def save(state: dict[str, Any], path: Path | str | None = None) -> None:
    """Записывает всё состояние в файл одним вызовом."""
    _save_raw(state, Path(path) if path else DEFAULT_PATH)


def mark(
    chat_id: int, status: str, reason: str = "", path: Path | str | None = None
) -> dict[str, Any]:
    """Загружает, обновляет одну запись и сохраняет (load + apply + save). Возвращает состояние."""
    p = Path(path) if path else DEFAULT_PATH
    state = _load_raw(p)
    apply(state, chat_id, status, reason)
    _save_raw(state, p)
    return state


def mark_sent(chat_id: int, path: Path | str | None = None) -> dict[str, Any]:
    return mark(chat_id, STATUS_SENT, "", path)


def mark_skipped(chat_id: int, reason: str, path: Path | str | None = None) -> dict[str, Any]:
    return mark(chat_id, STATUS_SKIPPED, reason, path)


def mark_error(chat_id: int, reason: str, path: Path | str | None = None) -> dict[str, Any]:
    return mark(chat_id, STATUS_ERROR, reason, path)


def should_skip(chat_id: int, path: Path | str | None = None) -> bool:
    """Проверка resume: уже отправлено? (читает файл)."""
    return should_skip_state(load(path), chat_id)


def report(path: Path | str | None = None) -> str:
    """Строковый отчёт по текущему файлу прогресса."""
    return report_from(load(path))


def reset(path: Path | str | None = None) -> None:
    """Удаляет файл прогресса (для --reset-progress)."""
    p = Path(path) if path else DEFAULT_PATH
    if p.exists():
        p.unlink()
        log.info("Прогресс сброшен: %s", p)
