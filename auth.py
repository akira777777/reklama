"""Создание и загрузка Telethon-сессии, интерактивный логин."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from telethon import TelegramClient

import config

log = logging.getLogger(__name__)

__all__ = ["get_client", "start", "client_session"]


def get_client() -> TelegramClient:
    """Возвращает настроенный TelegramClient (ещё не подключённый).

    Бросает RuntimeError, если API_ID/API_HASH не заданы в .env.
    """
    if not config.has_credentials():
        raise RuntimeError(
            "TELEGRAM_API_ID / TELEGRAM_API_HASH не заданы. "
            "Скопируйте .env.example в .env и заполните значения "
            "(получить: https://my.telegram.org)."
        )
    log.info(
        "Сессия: %s (файл %s.session рядом со скриптом).",
        config.SESSION_NAME,
        config.SESSION_NAME,
    )
    client = TelegramClient(
        config.SESSION_PATH,
        config.API_ID,
        config.API_HASH,
    )
    return client


async def start(client: TelegramClient) -> None:
    """Подключает клиента; при первом запуске — интерактивный ввод телефона/кода/2FA.

    `client.start()` сам сохраняет файл сессии и повторно его использует.
    """
    await client.start()
    me = await client.get_me()
    log.info(
        "Вошли как: %s (id=%s).",
        getattr(me, "username", None) or me.first_name,
        me.id,
    )


@asynccontextmanager
async def client_session() -> AsyncGenerator[TelegramClient, None]:
    """Async context manager: создаёт, подключает и корректно отключает клиента."""
    client = get_client()
    await start(client)
    try:
        yield client
    finally:
        await client.disconnect()
        log.info("Клиент отключён.")
