"""Утилиты проекта: общие функции, используемые несколькими модулями."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telethon import TelegramClient

from . import config

log = logging.getLogger(__name__)

# Управляющие символы в названиях чатов (задаются админами групп) — нейтрализуем
# для защиты логов/терминала от инъекций (CWE-117).
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def setup_logging(log_name: str) -> Path:
    """Настраивает логирование в консоль + в logs/<log_name>_<timestamp>.log.

    Возвращает путь к файлу лога.
    """
    logs_dir = config.BASE_DIR / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / f"{log_name}_{ts}.log"
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Не дублируем хендлеры при повторном вызове
    if not root.handlers:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        if hasattr(sh.stream, "reconfigure"):
            sh.stream.reconfigure(encoding="utf-8")
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(sh)
        root.addHandler(fh)
    return log_file


def clean_control_chars(title: str) -> str:
    """Удаляет управляющие символы ASCII из строки для безопасного вывода."""
    return _CONTROL_RE.sub(" ", str(title))


@asynccontextmanager
async def managed_telegram_client() -> AsyncGenerator[TelegramClient, None]:
    """Async context manager для Telethon-клиента.

    Создаёт, подключает и корректно отключает клиента.
    При первом запуске — интерактивный логин.
    """
    from . import auth

    client = auth.get_client()
    await auth.start(client)
    try:
        yield client
    finally:
        await client.disconnect()
        log.info("Клиент отключён.")


def mutate_message(text: str) -> str:
    """Добавляет случайный невидимый хвост из zero-width символов к сообщению.

    Это делает хэш каждого отправленного сообщения уникальным для обхода
    сигнатурных спам-фильтров Telegram, оставаясь невидимым для пользователей.
    """
    import random

    if not config.MUTATE_MESSAGE or not text:
        return text
    # Набор невидимых символов: zero-width space, zero-width non-joiner, zero-width joiner, BOM
    invisible_chars = ["\u200b", "\u200c", "\u200d", "\ufeff"]
    suffix = "".join(random.choices(invisible_chars, k=random.randint(1, 5)))
    return text + suffix
