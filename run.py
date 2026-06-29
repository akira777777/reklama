"""CLI-оркестратор рассылки: argparse + основной цикл с задержками и resume."""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import re
import sys
from datetime import datetime

from reklama import auth, config, dialogs, emoji, progress, sender
from reklama.sender import SendResult
from reklama.utils import mutate_message, setup_logging

log = logging.getLogger("run")


# Управляющие символы в названиях чатов (задаются админами групп) — нейтрализуем
# для защиты логов/терминала от инъекций (CWE-117).
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _clean(title: str) -> str:
    return _CONTROL_RE.sub(" ", str(title))


def resolve_spintax(text: str) -> str:
    """Разрешает spintax вида {вариант1|вариант2|вариант3}.

    Поддерживает вложенность, например {Привет|Здравствуйте {друг|коллега}}.
    """
    pattern = re.compile(r"\{([^{}]+)\}")
    while True:
        match = pattern.search(text)
        if not match:
            break
        options = match.group(1).split("|")
        choice = random.choice(options)
        text = text[: match.start()] + choice + text[match.end() :]
    return text


async def _record(state: dict, eid: int, status: str, reason: str) -> None:
    """Обновляет состояние в памяти и асинхронно (без блокировки цикла) пишет файл."""
    progress.apply(state, eid, status, reason)
    await asyncio.to_thread(progress.save, state)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Рассылка рекламы по группам Telegram (Telethon).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Показать список групп и пропуски без отправки.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Ограничить число групп (для дымового теста).",
    )
    p.add_argument(
        "--reset-progress",
        action="store_true",
        help="Удалить progress.json перед запуском.",
    )
    return p.parse_args()


def read_message() -> str:
    path = config.BASE_DIR / config.MESSAGE_FILE
    if not path.exists():
        log.error("Файл сообщения не найден: %s", path)
        sys.exit(1)
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        log.error("Файл сообщения пустой: %s", path)
        sys.exit(1)
    return text


def within_active_window(window: config.ActiveWindow) -> bool:
    now = datetime.now()
    cur = now.hour * 60 + now.minute
    if window.start_min <= window.end_min:
        return window.start_min <= cur < window.end_min
    return cur >= window.start_min or cur < window.end_min


def seconds_until_window(window: config.ActiveWindow) -> int:
    now = datetime.now()
    cur = now.hour * 60 + now.minute
    target = window.start_min  # даже при переходе через полночь ждём начала окна
    delta = target - cur
    if delta <= 0:
        delta += 24 * 60
    return delta * 60


async def ensure_active(window: config.ActiveWindow | None) -> None:
    """Если задано окно активности и сейчас вне его — ждём до открытия."""
    if window is None:
        return
    while not within_active_window(window):
        wait = max(60, seconds_until_window(window))
        log.warning("Вне окна активности (%s) — ждём %d сек.", config.ACTIVE_HOURS, wait)
        await asyncio.sleep(min(wait, 3600))


async def run() -> None:
    args = parse_args()
    log_file = setup_logging("run")
    log.info("Лог запуска: %s", log_file)

    if args.reset_progress:
        progress.reset()

    text_template = read_message()
    media = config.resolve_media_path()
    if media:
        log.info("Медиа: %s (вид: %s)", media, sender.detect_media_kind(media))
    else:
        log.info("Медиа не найдено — рассылка только текстом.")

    window = config.parse_active_hours(config.ACTIVE_HOURS)
    if window:
        log.info("Окно активности: %s", config.ACTIVE_HOURS)

    async with auth.client_session() as client:
        if not args.dry_run:
            if not await auth.check_self(client):
                log.critical("Самопроверка аккаунта не удалась. Завершаем работу во избежание банов.")
                sys.exit(1)

        groups = await dialogs.collect_groups(client)
        if args.limit is not None:
            groups = groups[: args.limit]
        total = len(groups)
        log.info("Кандидатов к рассылке: %d.", total)
        if total == 0:
            log.warning("Группы не найдены — нечего рассылать.")
            return

        state = progress.load()

        if args.dry_run:
            log.info("=== DRY RUN ===")
            sample_resolved = resolve_spintax(text_template)
            sample_clean, _ = emoji.parse_custom_emoji(sample_resolved)
            log.info("Пример сообщения после spintax:\n---\n%s\n---", sample_clean)
            skipped_count = sum(
                1 for eid, _title, _entity in groups if progress.should_skip_state(state, eid)
            )
            for eid, title, _entity in groups:
                flag = " [уже отправлено]" if progress.should_skip_state(state, eid) else ""
                log.info("  - %s (id=%d)%s", _clean(title), eid, flag)
            log.info("Будет пропущено (resume): %d из %d.", skipped_count, total)
            return

        done = 0
        delay_multiplier = 1.0
        for i, (eid, title, entity) in enumerate(groups):
            if progress.should_skip_state(state, eid):
                log.info("[%d/%d] ПРОПУСК (resume): %s", i + 1, total, _clean(title))
                continue

            await ensure_active(window)
            log.info("[%d/%d] Отправка в: %s (id=%d)", i + 1, total, _clean(title), eid)

            resolved_text = resolve_spintax(text_template)
            final_text, entities = emoji.parse_custom_emoji(resolved_text)
            final_text = mutate_message(final_text)
            formatting_entities = entities if entities else None

            result: SendResult = await sender.send(
                client, entity, final_text, media, formatting_entities=formatting_entities
            )

            if result.ok:
                await _record(state, eid, progress.STATUS_SENT, result.reason)
                extra = f" ({result.reason})" if result.reason else ""
                log.info("[%d/%d] ОТПРАВЛЕНО%s.", i + 1, total, extra)
                if "after_floodwait" in result.reason:
                    delay_multiplier = min(4.0, delay_multiplier * 1.5)
                    log.info(
                        "Множитель задержек увеличен до %.2fx из-за FloodWait",
                        delay_multiplier,
                    )
                else:
                    if delay_multiplier > 1.0:
                        delay_multiplier = max(1.0, delay_multiplier - 0.1)
                        log.info("Снижаем множитель задержек до %.2fx", delay_multiplier)
            elif result.status == progress.STATUS_SKIPPED:
                await _record(state, eid, progress.STATUS_SKIPPED, result.reason)
                log.warning("[%d/%d] ПРОПУСК: %s", i + 1, total, result.reason)
                if "FloodWait" in result.reason:
                    delay_multiplier = min(4.0, delay_multiplier * 2.0)
                    log.info(
                        "Множитель задержек увеличен до %.2fx из-за FloodWait при пропуске",
                        delay_multiplier,
                    )
            else:
                await _record(state, eid, progress.STATUS_ERROR, result.reason)
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


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.warning("Прервано пользователем.")


if __name__ == "__main__":
    main()
