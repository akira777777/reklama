"""CLI-оркестратор рассылки: argparse + основной цикл с задержками и resume."""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import re
import sys
from datetime import datetime
from pathlib import Path

import auth
import config
import dialogs
import emoji
import progress
import sender
from sender import SendResult

log = logging.getLogger("run")

# Управляющие символы в названиях чатов (задаются админами групп) — нейтрализуем
# для защиты логов/терминала от инъекций (CWE-117).
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _clean(title: str) -> str:
    return _CONTROL_RE.sub(" ", str(title))


async def _record(state: dict, eid: int, status: str, reason: str) -> None:
    """Обновляет состояние в памяти и асинхронно (без блокировки цикла) пишет файл."""
    progress.apply(state, eid, status, reason)
    await asyncio.to_thread(progress.save, state)


def setup_logging() -> Path:
    """Настраивает логирование в консоль + в logs/<timestamp>.log. Возвращает путь лога."""
    logs_dir = config.BASE_DIR / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / f"{ts}.log"
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Не дублируем хендлеры при повторном вызове.
    if not root.handlers:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(sh)
        root.addHandler(fh)
    return log_file


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
    log_file = setup_logging()
    log.info("Лог запуска: %s", log_file)

    if args.reset_progress:
        progress.reset()

    text = read_message()
    media = config.resolve_media_path()
    if media:
        log.info("Медиа: %s (вид: %s)", media, sender.detect_media_kind(media))
    else:
        log.info("Медиа не найдено — рассылка только текстом.")

    window = config.parse_active_hours(config.ACTIVE_HOURS)
    if window:
        log.info("Окно активности: %s", config.ACTIVE_HOURS)

    client = auth.get_client()
    await auth.start(client)
    try:
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
            resume = []
            for eid, title, _entity in groups:
                if progress.should_skip_state(state, eid):
                    resume.append((eid, title))
            for eid, title, _entity in groups:
                flag = " [уже отправлено]" if progress.should_skip_state(state, eid) else ""
                print(f"  - {_clean(title)} (id={eid}){flag}")
            log.info("Будет пропущено (resume): %d из %d.", len(resume), total)
            return

        done = 0
        for i, (eid, title, entity) in enumerate(groups):
            if progress.should_skip_state(state, eid):
                log.info("[%d/%d] ПРОПУСК (resume): %s", i + 1, total, _clean(title))
                continue

            await ensure_active(window)
            log.info("[%d/%d] Отправка в: %s (id=%d)", i + 1, total, _clean(title), eid)
            result: SendResult = await sender.send(client, entity, text, media)

            if result.ok:
                await _record(state, eid, progress.STATUS_SENT, "")
                extra = f" ({result.reason})" if result.reason else ""
                log.info("[%d/%d] ОТПРАВЛЕНО%s.", i + 1, total, extra)
            elif result.status == progress.STATUS_SKIPPED:
                await _record(state, eid, progress.STATUS_SKIPPED, result.reason)
                log.warning("[%d/%d] ПРОПУСК: %s", i + 1, total, result.reason)
            else:
                await _record(state, eid, progress.STATUS_ERROR, result.reason)
                log.error("[%d/%d] ОШИБКА: %s", i + 1, total, result.reason)

            done += 1
            if i != total - 1:
                if done % config.BATCH_SIZE == 0:
                    pause = random.randint(
                        min(config.BATCH_PAUSE_MIN_SEC, config.BATCH_PAUSE_MAX_SEC),
                        max(config.BATCH_PAUSE_MIN_SEC, config.BATCH_PAUSE_MAX_SEC),
                    )
                    log.info("Перерыв пакета (каждые %d): %d сек.", config.BATCH_SIZE, pause)
                    await asyncio.sleep(pause)
                else:
                    delay = random.randint(
                        min(config.DELAY_MIN_SEC, config.DELAY_MAX_SEC),
                        max(config.DELAY_MIN_SEC, config.DELAY_MAX_SEC),
                    )
                    await asyncio.sleep(delay)

        log.info(progress.report())
    finally:
        await client.disconnect()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.warning("Прервано пользователем.")


if __name__ == "__main__":
    main()
