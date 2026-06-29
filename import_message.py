"""Скрипт для импорта сообщения (текст, форматирование, кастомные эмодзи и медиа) из Telegram."""

from __future__ import annotations

import argparse
import asyncio
import copy
import logging
import struct
from pathlib import Path

from telethon.extensions import markdown
from telethon.tl.types import MessageEntityCustomEmoji

from reklama import auth, config
from reklama.utils import clean_control_chars, setup_logging

log = logging.getLogger("import_message")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Импорт рекламного сообщения (с медиа и эмодзи) из Telegram-чата.",
    )
    p.add_argument(
        "-c",
        "--chat",
        type=str,
        default="me",
        help="Имя чата, юзернейм или 'me' для Избранного (Saved Messages). По умолчанию: 'me'",
    )
    p.add_argument(
        "-id",
        "--message-id",
        type=int,
        default=None,
        help="ID конкретного сообщения. Если не задан, берется последнее сообщение из чата.",
    )
    return p.parse_args()


def prepare_message_for_saving(raw_text: str, entities: list | None) -> tuple[str, list]:
    """Заменяет MessageEntityCustomEmoji на текстовые теги [emoji:doc_id]
    и корректирует смещения (offset) для остальных сущностей, учитывая UTF-16 кодировку Telegram.
    """
    if not entities:
        return raw_text, []

    copied_entities = copy.deepcopy(entities)

    new_entities = [e for e in copied_entities if not isinstance(e, MessageEntityCustomEmoji)]
    emoji_entities = [e for e in copied_entities if isinstance(e, MessageEntityCustomEmoji)]

    if not emoji_entities:
        return raw_text, new_entities

    # Сортируем эмодзи справа налево (по убыванию offset), чтобы замена не ломала
    # соответствие индексов следующих заменяемых эмодзи.
    emoji_entities.sort(key=lambda e: e.offset, reverse=True)

    # Превращаем текст в список 16-битных код-юнитов UTF-16
    raw_bytes = raw_text.encode("utf-16-le")
    num_units = len(raw_bytes) // 2
    code_units = list(struct.unpack(f"<{num_units}H", raw_bytes))

    for emoji in emoji_entities:
        offset = emoji.offset
        length = emoji.length
        doc_id = emoji.document_id

        tag = f"[emoji:{doc_id}]"
        tag_bytes = tag.encode("utf-16-le")
        tag_units = list(struct.unpack(f"<{len(tag_bytes) // 2}H", tag_bytes))

        diff = len(tag_units) - length

        # Производим замену в списке код-юнитов
        code_units[offset : offset + length] = tag_units

        # Корректируем смещения для всех остальных сущностей
        for other in new_entities:
            if other.offset >= offset + length:
                other.offset += diff
            elif other.offset >= offset:
                other.offset = offset
                other.length = max(0, other.length + diff)
            elif other.offset + other.length > offset:
                other.length += diff

    # Декодируем обратно в Python-строку
    new_bytes = struct.pack(f"<{len(code_units)}H", *code_units)
    new_text = new_bytes.decode("utf-16-le")

    return new_text, new_entities


async def _run_import(client, args: argparse.Namespace) -> None:  # noqa: ANN001
    """Основная логика импорта сообщения."""
    # Находим сущность чата
    try:
        entity = await client.get_entity(args.chat)
    except Exception as e:  # noqa: BLE001
        log.error("Не удалось найти чат '%s': %s", args.chat, repr(e))
        return

    # Находим сообщение
    if args.message_id:
        messages = await client.get_messages(entity, ids=args.message_id)
        message = messages if messages else None
    else:
        messages = await client.get_messages(entity, limit=1)
        message = messages[0] if messages else None

    if not message:
        log.error("Сообщение не найдено.")
        return

    log.info("Найдено сообщение (id=%d). Обработка...", message.id)

    # 1. Скачиваем медиа при наличии
    media_file = None
    if message.media:
        media_dir = config.BASE_DIR / "media"
        if media_dir.exists():
            log.info("Очищаем папку 'media/' от старых файлов...")
            for f in media_dir.iterdir():
                if f.is_file():
                    try:
                        f.unlink()
                    except Exception as e:  # noqa: BLE001
                        log.warning("Не удалось удалить старый файл %s: %s", f, e)
        else:
            media_dir.mkdir(parents=True, exist_ok=True)

        log.info("Скачиваем медиа-файл...")
        downloaded_path = await message.download_media(file=media_dir)
        if downloaded_path:
            media_file = Path(downloaded_path).name
            log.info("Медиа успешно сохранено: media/%s", media_file)
        else:
            log.warning("Медиа найдено, но скачать его не удалось.")

    # 2. Обрабатываем текст и эмодзи
    raw_text = message.message or ""
    new_text, new_entities = prepare_message_for_saving(raw_text, message.entities)
    markdown_text = markdown.unparse(new_text, new_entities)

    # 3. Сохраняем в message.txt
    message_file_path = config.BASE_DIR / config.MESSAGE_FILE
    message_file_path.write_text(markdown_text, encoding="utf-8")
    log.info("Текст успешно сохранен в %s", config.MESSAGE_FILE)

    log.info("=== ИМПОРТ УСПЕШНО ЗАВЕРШЕН ===")
    log.info("Итоговый текст для отправки:\n---\n%s\n---", clean_control_chars(markdown_text))
    if media_file:
        log.info("Итоговое медиа: media/%s", media_file)


async def main() -> None:
    args = parse_args()
    log_file = setup_logging("import")
    log.info("Лог импорта: %s", log_file)
    log.info("Подключаемся для импорта из чата '%s'...", args.chat)

    async with auth.client_session() as client:
        await _run_import(client, args)


def entrypoint() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.warning("Импорт прерван пользователем.")


if __name__ == "__main__":
    entrypoint()
