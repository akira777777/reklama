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

    status: str  # "sent" | "skipped" | "error"
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "sent"


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


async def send(client: Any, entity: Any, text: str, media_path: str | None) -> SendResult:
    """Отправляет сообщение (с медиа или без) и классифицирует результат.

    При FloodWaitError ждёт требуемое время и повторяет отправку один раз.
    """

    async def _do_send() -> None:
        if media_path:
            kind = detect_media_kind(media_path)
            force_document = kind == "document"
            await client.send_file(entity, media_path, caption=text, force_document=force_document)
        else:
            await client.send_message(entity, text)

    try:
        await _do_send()
    except FloodWaitError as e:
        wait = int(getattr(e, "seconds", 60)) + 5
        log.warning("FloodWait: ждём %d сек перед повтором.", wait)
        await asyncio.sleep(wait)
        try:
            await _do_send()
        except SKIPPED_ERRORS as e2:
            return SendResult("skipped", type(e2).__name__)
        except Exception as e2:  # noqa: BLE001 — классифицируем всё прочее
            return SendResult("error", repr(e2))
        return SendResult("sent", "after_floodwait")
    except SKIPPED_ERRORS as e:
        return SendResult("skipped", type(e).__name__)
    except Exception as e:  # noqa: BLE001 — классифицируем всё прочее
        return SendResult("error", repr(e))
    return SendResult("sent")
