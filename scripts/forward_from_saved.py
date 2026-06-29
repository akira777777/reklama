"""Скрипт для пересылки сообщения из Избранного в группы с сохранением премиум-эмодзи.

Этот скрипт объединяет логику import_message.py и run.py для предоставления
одношагового рабочего процесса "Импорт из Избранного + Отправка по группам".
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import sys
from pathlib import Path

# Добавляем корневую директорию проекта в sys.path, чтобы можно было импортировать reklama
sys.path.append(str(Path(__file__).resolve().parent.parent))

from import_message import prepare_message_for_saving
from reklama import auth, config, dialogs, emoji, progress, sender
from reklama.sender import SendResult
from reklama.spintax import resolve_spintax
from reklama.utils import mutate_message, setup_logging

# Настройка логирования с кодировкой UTF-8 для корректной обработки русских символов
log = logging.getLogger("forward_from_saved")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Пересылка сообщения из Избранного в группы с сохранением премиум-эмодзи.",
    )
    p.add_argument(
        "-id",
        "--message-id",
        type=int,
        default=None,
        help="ID сообщения в Избранном. Если не указан, используется последнее сообщение.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Показать группы назначения без отправки.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Ограничить количество групп (для тестирования).",
    )
    p.add_argument(
        "--reset-progress",
        action="store_true",
        help="Сбросить прогресс перед отправкой.",
    )
    return p.parse_args()


async def import_message_from_saved(client, message_id: int | None = None) -> tuple[str, list | None]:
    """Импорт сообщения из 'me' (Избранные сообщения) и подготовка к отправке.
    
    Возвращает:
        кортеж: (текст, formatting_entities)
    """
    log.info("Importing message from Saved Messages (me)...")
    
    # Get the 'me' entity
    try:
        entity = await client.get_entity("me")
    except Exception as e:
        log.error("Failed to get 'me' entity: %s", repr(e))
        sys.exit(1)
    
    # Get the message
    if message_id:
        messages = await client.get_messages(entity, ids=message_id)
        message = messages if messages else None
    else:
        messages = await client.get_messages(entity, limit=1)
        message = messages[0] if messages else None

    if not message:
        log.error("Message not found in Saved Messages.")
        sys.exit(1)

    log.info("Found message (id=%d). Processing...", message.id)

    # Handle media
    media_file = None
    if message.media:
        media_dir = config.BASE_DIR / "media"
        if media_dir.exists():
            log.info("Cleaning 'media/' directory...")
            for f in media_dir.iterdir():
                if f.is_file():
                    try:
                        f.unlink()
                    except Exception as e:
                        log.warning("Failed to delete old file %s: %s", f, e)
        else:
            media_dir.mkdir(parents=True, exist_ok=True)

        log.info("Downloading media...")
        downloaded_path = await message.download_media(file=media_dir)
        if downloaded_path:
            media_file = Path(downloaded_path).name
            log.info("Media saved: media/%s", media_file)
        else:
            log.warning("Media found but failed to download.")
    else:
        log.info("No media in message.")

    # Process text and entities
    raw_text = message.message or ""
    if not raw_text and not message.media:
        log.error("Message has no text and no media.")
        sys.exit(1)
        
    processed_text, processed_entities = prepare_message_for_saving(raw_text, message.entities)
    
    # Save to message.txt (to maintain compatibility with existing workflow)
    message_file_path = config.BASE_DIR / config.MESSAGE_FILE
    message_file_path.write_text(processed_text, encoding="utf-8")
    log.info("Message text saved to %s", config.MESSAGE_FILE)
    
    # If there was media, make sure it's set in config
    if media_file:
        # This is a bit of a hack, but it ensures the send process finds the media
        import os
        os.environ["MEDIA_PATH"] = str(config.BASE_DIR / "media" / media_file)
        log.info("Set MEDIA_PATH to %s", os.environ["MEDIA_PATH"])
    
    return processed_text, processed_entities


async def send_to_targets(client, text: str, entities: list | None) -> None:
    """Отправка сообщения в группы назначения."""
    args = parse_args()  # Повторный парсинг для аргументов отправки
    
    if args.reset_progress:
        progress.reset()

    media = config.resolve_media_path()
    if media:
        log.info("Медиа: %s (тип: %s)", media, sender.detect_media_kind(media))
    else:
        log.info("Медиа не найдено - рассылка только текстом.")

    groups = await dialogs.collect_groups(client)
    if args.limit is not None:
        groups = groups[: args.limit]
    total = len(groups)
    log.info("Группы для рассылки: %d.", total)
    if total == 0:
        log.warning("Группы не найдены - нечего отправлять.")
        return

    state = progress.load()

    if args.dry_run:
        log.info("=== ТЕСТОВЫЙ ЗАПУСК ===")
        sample_resolved = resolve_spintax(text)
        sample_clean, _ = emoji.parse_custom_emoji(sample_resolved)
        log.info("Пример сообщения после spintax:\n---\n%s\n---", sample_clean)
        skipped_count = sum(
            1 for eid, _title, _entity in groups if progress.should_skip_state(state, eid)
        )
        for eid, title, _entity in groups:
            flag = " [уже отправлено]" if progress.should_skip_state(state, eid) else ""
            log.info("  - %s (id=%d)%s", title, eid, flag)
        log.info("Будет пропущено (resume): %d из %d.", skipped_count, total)
        return

    done = 0
    delay_multiplier = 1.0
    for i, (eid, title, entity) in enumerate(groups):
        if progress.should_skip_state(state, eid):
            log.info("[%d/%d] ПРОПУСК (resume): %s", i + 1, total, title)
            continue

        log.info("[%d/%d] Отправка в: %s (id=%d)", i + 1, total, title, eid)

        resolved_text = resolve_spintax(text)
        final_text, final_entities = emoji.parse_custom_emoji(resolved_text)
        final_text = mutate_message(final_text)
        formatting_entities = final_entities if final_entities else None

        result: SendResult = await sender.send(
            client, entity, final_text, media, formatting_entities=formatting_entities
        )

        if result.ok:
            progress.apply(state, eid, progress.STATUS_SENT, result.reason)
            progress.save(state)  # Синхронное сохранение для простоты
            extra = f" ({result.reason})" if result.reason else ""
            log.info("[%d/%d] ОТПРАВЛЕНО%s.", i + 1, total, extra)
        elif result.status == progress.STATUS_SKIPPED:
            progress.apply(state, eid, progress.STATUS_SKIPPED, result.reason)
            progress.save(state)
            log.warning("[%d/%d] ПРОПУСК: %s", i + 1, total, result.reason)
        else:
            progress.apply(state, eid, progress.STATUS_ERROR, result.reason)
            progress.save(state)
            log.error("[%d/%d] ОШИБКА: %s", i + 1, total, result.reason)

        done += 1
        if i != total - 1:
            if done % config.BATCH_SIZE == 0:
                base_pause = random.randint(
                    min(config.BATCH_PAUSE_MIN_SEC, config.BATCH_PAUSE_MAX_SEC),
                    max(config.BATCH_PAUSE_MIN_SEC, config.BATCH_PAUSE_MAX_SEC),
                )
                pause = int(base_pause * delay_multiplier)
                log.info(
                    "Перерыв пакета (каждые %d): %d сек (базовый %d сек, множитель %.2fx).",
                    config.BATCH_SIZE,
                    pause,
                    base_pause,
                    delay_multiplier,
                )
                await asyncio.sleep(pause)
            else:
                base_delay = random.randint(
                    min(config.DELAY_MIN_SEC, config.DELAY_MAX_SEC),
                    max(config.DELAY_MIN_SEC, config.DELAY_MAX_SEC),
                )
                delay = int(base_delay * delay_multiplier)
                log.info(
                    "Задержка перед следующим: %d сек (базовый %d сек, множитель %.2fx).",
                    delay,
                    base_delay,
                    delay_multiplier,
                )
                await asyncio.sleep(delay)

    log.info(progress.report())


async def main() -> None:
    args = parse_args()
    log_file = setup_logging("forward_from_saved")
    log.info("Forward log: %s", log_file)

    async with auth.client_session() as client:
        if not args.dry_run and not await auth.check_self(client):
            log.critical("Самопроверка аккаунта не удалась. Завершаем работу во избежание банов.")
            sys.exit(1)

        # Import message from Saved Messages
        text, entities = await import_message_from_saved(client, args.message_id)
        
        # Send to targets
        await send_to_targets(client, text, entities)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.warning("Interrupted by user.")
    except Exception as e:
        log.error("Unexpected error: %s", repr(e))
        sys.exit(1)