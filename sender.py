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
        except FloodWaitError as e2:
            # Повторный FloodWait: уважаем требование, ждём и помечаем как пропуск.
            wait2 = int(getattr(e2, "seconds", 60)) + 5
            log.warning("Повторный FloodWait: ждём %d сек и пропускаем чат.", wait2)
            await asyncio.sleep(wait2)
            return SendResult(progress.STATUS_SKIPPED, "FloodWait")
        except SKIPPED_ERRORS as e3:
            return SendResult(progress.STATUS_SKIPPED, type(e3).__name__)
        except Exception as e3:  # noqa: BLE001 — классифицируем всё прочее
            return SendResult(progress.STATUS_ERROR, repr(e3))
        return SendResult(progress.STATUS_SENT, "after_floodwait")
    except SKIPPED_ERRORS as e:
        return SendResult(progress.STATUS_SKIPPED, type(e).__name__)
    except Exception as e:  # noqa: BLE001 — классифицируем всё прочее
        return SendResult(progress.STATUS_ERROR, repr(e))
    return SendResult(progress.STATUS_SENT)
