"""Сборка сообщения, отправка медиа, классификация ошибок."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from dataclasses import dataclass
from typing import Any

from telethon.errors import (
    ChannelPrivateError,
    ChatWriteForbiddenError,
    FloodWaitError,
    SlowModeWaitError,
    UserBannedInChannelError,
)

import config
import progress

log = logging.getLogger(__name__)

# Ошибки, при которых чат пропускается (прав нет / slow-mode / нет доступа).
SKIPPED_ERRORS: tuple[type[Exception], ...] = (
    ChatWriteForbiddenError,
    UserBannedInChannelError,
    ChannelPrivateError,
    SlowModeWaitError,
)


@dataclass(frozen=True)
class SendResult:
    """Результат отправки в один чат."""

    status: str  # progress.STATUS_SENT | STATUS_SKIPPED | STATUS_ERROR
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status == progress.STATUS_SENT


def detect_media_kind(path: str) -> str:
    """Определяет вид медиа по расширению/mimetype: photo / video / document.

    FORCE_DOCUMENT принудительно возвращает "document".
    """
    if config.FORCE_DOCUMENT:
        return "document"
    mime, _ = mimetypes.guess_type(path)
    if mime:
        if mime.startswith("image/"):
            return "photo"
        if mime.startswith("video/"):
            return "video"
    return "document"


async def send(
    client: Any,
    entity: Any,
    text: str,
    media_path: str | None,
    formatting_entities: list[Any] | None = None,
) -> SendResult:
    """Отправляет сообщение (с медиа или без) и классифицирует результат.

    formatting_entities — напр. кастомные эмодзи (MessageEntityCustomEmoji).
    При FloodWaitError ждёт требуемое время и повторяет отправку (до 5 раз).
    При ошибке отправки медиа автоматически отправляет только текст.
    """

    async def _send_media() -> None:
        assert media_path is not None
        kind = detect_media_kind(media_path)
        force_document = kind == "document"
        await client.send_file(
            entity,
            media_path,
            caption=text,
            force_document=force_document,
            formatting_entities=formatting_entities,
        )

    async def _send_text() -> None:
        await client.send_message(
            entity, text, formatting_entities=formatting_entities
        )

    async def _do_send() -> tuple[str, str]:
        if media_path:
            try:
                await _send_media()
                return progress.STATUS_SENT, "with_media"
            except (FloodWaitError, SlowModeWaitError, ChatWriteForbiddenError, UserBannedInChannelError, ChannelPrivateError) as e:
                # Специальные ошибки пропускаем выше для внешней обработки
                raise e
            except Exception as e:
                log.warning("Ошибка отправки медиа в %s (%s). Пробуем отправить только текст.", entity, repr(e))
                await _send_text()
                return progress.STATUS_SENT, f"text_fallback: {type(e).__name__}"
        else:
            await _send_text()
            return progress.STATUS_SENT, "text_only"

    attempts = 0
    while True:
        try:
            status, reason = await _do_send()
            if attempts > 0:
                reason = f"{reason}_after_floodwait"
            return SendResult(status, reason)
        except FloodWaitError as e:
            attempts += 1
            wait = int(getattr(e, "seconds", 60)) + 5
            log.warning("FloodWait (попытка %d из 5): ждём %d сек.", attempts, wait)
            await asyncio.sleep(wait)
            if attempts >= 5:
                log.error("Превышено число попыток обхода FloodWait (5). Пропускаем чат.")
                return SendResult(progress.STATUS_SKIPPED, "FloodWaitLimitExceeded")
        except SKIPPED_ERRORS as e:
            return SendResult(progress.STATUS_SKIPPED, type(e).__name__)
        except Exception as e:  # noqa: BLE001 — классифицируем всё прочее
            return SendResult(progress.STATUS_ERROR, repr(e))
