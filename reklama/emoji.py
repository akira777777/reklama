"""Поддержка кастомных/анимированных Telegram-эмодзи прямо в тексте сообщения.

Синтаксис в message.txt:
    Привет! [emoji:5377647729285175351]

Маркер ``[emoji:<document_id>]`` заменяется в итоговом тексте на один базовый
символ-эмодзи, поверх которого навешивается сущность ``MessageEntityCustomEmoji``.
Получатель видит анимированный кастомный эмодзи (document_id берут из
@TelegramStickers / кастомных эмодзи-сетов). Базовый символ — запасной вариант
на случай, если клиент не умеет рисовать кастомный эмодзи.

Если маркеров нет — функция ничего не меняет (обратная совместимость).
"""

from __future__ import annotations

import logging
import re

from telethon.tl.types import MessageEntityCustomEmoji

log = logging.getLogger(__name__)

# Маркер [emoji:document_id]. document_id — большое целое.
CUSTOM_EMOJI_RE = re.compile(r"\[emoji:(\d+)\]")

# Базовый символ-заменитель (один кодпоинт → length=1 в entities).
PLACEHOLDER = "\u2764"  # ❤


def parse_custom_emoji(text: str) -> tuple[str, list[MessageEntityCustomEmoji]]:
    """Разбирает маркеры [emoji:doc_id] в тексте.

    Возвращает (текст_with_placeholders, [сущности]). Сущности отсортированы
    по offset; offset/length рассчитаны относительно итогового текста.
    """
    if not text:
        return text, []

    parts: list[str] = []
    entities: list[MessageEntityCustomEmoji] = []
    offset = 0
    last_end = 0

    for m in CUSTOM_EMOJI_RE.finditer(text):
        # Литеральный сегмент между маркерами копируется как есть.
        segment = text[last_end : m.start()]
        parts.append(segment)
        offset += len(segment)

        doc_id = int(m.group(1))
        parts.append(PLACEHOLDER)
        entities.append(
            MessageEntityCustomEmoji(offset=offset, length=len(PLACEHOLDER), document_id=doc_id)
        )
        offset += len(PLACEHOLDER)
        last_end = m.end()

    parts.append(text[last_end:])
    result = "".join(parts)

    if entities:
        log.info("Найдено кастомных эмодзи в сообщении: %d.", len(entities))
    return result, entities
