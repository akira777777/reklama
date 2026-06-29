"""Скрипт для пересылки сообщения из Избранного в группы с сохранением премиум-эмодзи.

Этот скрипт объединяет логику import_message.py и run.py для предоставления
одношагового рабочего процесса "Импорт из Избранного + Отправка по группам".
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import auth
import config
import dialogs
import emoji
import progress
import sender
from import_message import prepare_message_for_saving
from run import resolve_spintax
from sender import SendResult
from utils import setup_logging

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
    """Import a message from 'me' (Saved Messages) and prepare it for sending.
    
    Returns:
        tuple: (text, formatting_entities)
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
    """Send the message to target groups."""
    args = parse_args()  # Re-parse for send-specific args
    
    if args.reset_progress:
        progress.reset()

    media = config.resolve_media_path()
    if media:
        log.info("Media: %s (type: %s)", media, sender.detect_media_kind(media))
    else:
        log.info("No media found - text only broadcast.")

    groups = await dialogs.collect_groups(client)
    if args.limit is not None:
        groups = groups[: args.limit]
    total = len(groups)
    log.info("Target groups for broadcast: %d.", total)
    if total == 0:
        log.warning("No groups found - nothing to send.")
        return

    state = progress.load()

    if args.dry_run:
        log.info("=== DRY RUN ===")
        sample_resolved = resolve_spintax(text)
        sample_clean, _ = emoji.parse_custom_emoji(sample_resolved)
        log.info("Example message after spintax:\n---\n%s\n---", sample_clean)
        skipped_count = sum(
            1 for eid, _title, _entity in groups if progress.should_skip_state(state, eid)
        )
        for eid, title, _entity in groups:
            flag = " [already sent]" if progress.should_skip_state(state, eid) else ""
            log.info("  - %s (id=%d)%s", title, eid, flag)
        log.info("Will skip (resume): %d out of %d.", skipped_count, total)
        return

    done = 0
    delay_multiplier = 1.0
    for i, (eid, title, entity) in enumerate(groups):
        if progress.should_skip_state(state, eid):
            log.info("[%d/%d] SKIP (resume): %s", i + 1, total, title)
            continue

        log.info("[%d/%d] Sending to: %s (id=%d)", i + 1, total, title, eid)

        resolved_text = resolve_spintax(text)
        final_text, final_entities = emoji.parse_custom_emoji(resolved_text)
        formatting_entities = final_entities if final_entities else None

        result: SendResult = await sender.send(
            client, entity, final_text, media, formatting_entities=formatting_entities
        )

        if result.ok:
            progress.apply(state, eid, progress.STATUS_SENT, result.reason)
            progress.save(state)  # Synchronous save for simplicity
            extra = f" ({result.reason})" if result.reason else ""
            log.info("[%d/%d] SENT%s.", i + 1, total, extra)
        elif result.status == progress.STATUS_SKIPPED:
            progress.apply(state, eid, progress.STATUS_SKIPPED, result.reason)
            progress.save(state)
            log.warning("[%d/%d] SKIP: %s", i + 1, total, result.reason)
        else:
            progress.apply(state, eid, progress.STATUS_ERROR, result.reason)
            progress.save(state)
            log.error("[%d/%d] ERROR: %s", i + 1, total, result.reason)

        done += 1
        if i != total - 1:
            if done % config.BATCH_SIZE == 0:
                import random
                base_pause = random.randint(
                    min(config.BATCH_PAUSE_MIN_SEC, config.BATCH_PAUSE_MAX_SEC),
                    max(config.BATCH_PAUSE_MIN_SEC, config.BATCH_PAUSE_MAX_SEC),
                )
                pause = int(base_pause * delay_multiplier)
                log.info(
                    "Batch pause (every %d): %d sec (base %d sec, multiplier %.2fx).",
                    config.BATCH_SIZE,
                    pause,
                    base_pause,
                    delay_multiplier,
                )
                await asyncio.sleep(pause)
            else:
                import random
                base_delay = random.randint(
                    min(config.DELAY_MIN_SEC, config.DELAY_MAX_SEC),
                    max(config.DELAY_MIN_SEC, config.DELAY_MAX_SEC),
                )
                delay = int(base_delay * delay_multiplier)
                log.info(
                    "Delay before next: %d sec (base %d sec, multiplier %.2fx).",
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