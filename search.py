"""Скрипт для поиска публичных групп/супергрупп по ключевым словам и опционального авто-вступления."""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import sys
from datetime import datetime
from pathlib import Path

from telethon import functions
from telethon.errors import ChannelsTooMuchError, FloodWaitError, UsersTooMuchError
from telethon.tl import types as tl
from telethon.tl.functions.channels import JoinChannelRequest

import auth
import config

log = logging.getLogger("search")


def setup_logging() -> Path:
    """Настраивает логирование в консоль + в logs/search_<timestamp>.log."""
    logs_dir = config.BASE_DIR / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / f"search_{ts}.log"
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        if hasattr(sh.stream, "reconfigure"):
            sh.stream.reconfigure(encoding="utf-8")
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(sh)
        root.addHandler(fh)
    return log_file


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Поиск новых публичных групп и каналов в Telegram.",
    )
    p.add_argument(
        "-q",
        "--query",
        type=str,
        required=True,
        help="Ключевое слово для поиска.",
    )
    p.add_argument(
        "-l",
        "--limit",
        type=int,
        default=20,
        help="Ограничение количества результатов (по умолчанию: 20).",
    )
    p.add_argument(
        "-j",
        "--join",
        action="store_true",
        help="Автоматически вступать во все найденные группы.",
    )
    p.add_argument(
        "--delay-min",
        type=int,
        default=15,
        help="Минимальная задержка между вступлениями в секундах (по умолчанию: 15).",
    )
    p.add_argument(
        "--delay-max",
        type=int,
        default=30,
        help="Максимальная задержка между вступлениями в секундах (по умолчанию: 30).",
    )
    return p.parse_args()


def is_search_group(entity: tl.TypeChat) -> bool:
    """Проверяет, является ли сущность группой или супергруппой.

    Исключает broadcast-каналы (где писать нельзя обычным пользователям),
    пользователей и недоступные чаты.
    """
    if isinstance(entity, tl.ChatForbidden | tl.ChannelForbidden):
        return False
    if isinstance(entity, tl.Chat):  # Базовая группа
        return True
    if isinstance(entity, tl.Channel):  # Супергруппа или канал
        # megagroup=True означает супергруппу (чат). megagroup=False означает канал-трансляцию.
        return bool(getattr(entity, "megagroup", False))
    return False


async def run_search() -> None:
    args = parse_args()
    log_file = setup_logging()
    log.info("Лог поиска: %s", log_file)
    log.info("Поиск по запросу: '%s' (лимит: %d)", args.query, args.limit)

    client = auth.get_client()
    await auth.start(client)

    try:
        # 1. Получаем список диалогов, в которых мы уже состоим
        log.info("Загрузка ваших текущих диалогов...")
        dialogs = await client.get_dialogs()
        joined_ids = {d.id for d in dialogs}
        log.info("Вы состоите в %d диалогах.", len(joined_ids))

        # 2. Выполняем глобальный поиск
        result = await client(
            functions.contacts.SearchRequest(
                q=args.query,
                limit=args.limit,
            )
        )

        found_groups = []
        for chat in result.chats:
            if is_search_group(chat):
                found_groups.append(chat)

        log.info("Всего найдено подходящих групп: %d.", len(found_groups))
        if not found_groups:
            log.info("Групп по запросу не найдено.")
            return

        for idx, group in enumerate(found_groups, start=1):
            username = getattr(group, "username", None)
            username_str = f"@{username}" if username else "нет username"
            participants = getattr(group, "participants_count", None)
            part_str = f"{participants} уч." if participants is not None else "кол-во участников неизвестно"
            status = "[состоите]" if group.id in joined_ids else "[не состоите]"
            log.info(
                "%d. %s (id=%d, %s, %s, %s)",
                idx,
                group.title,
                group.id,
                username_str,
                part_str,
                status,
            )

        if not args.join:
            log.info("Режим вступления отключен. Для автоматического вступления запустите с флагом --join")
            return

        # 3. Вступаем в группы, в которых еще не состоим
        to_join = [g for g in found_groups if g.id not in joined_ids]
        if not to_join:
            log.info("Вы уже состоите во всех найденных группах.")
            return

        log.info("Начинаем вступление в группы (всего к вступлению: %d)...", len(to_join))
        joined_count = 0

        for idx, group in enumerate(to_join, start=1):
            username = getattr(group, "username", None)
            identifier = f"@{username}" if username else f"id={group.id}"
            log.info("[%d/%d] Вступаем в '%s' (%s)...", idx, len(to_join), group.title, identifier)

            try:
                if isinstance(group, tl.Channel):
                    await client(JoinChannelRequest(group))
                    log.info("Успешно вступили в '%s'", group.title)
                    joined_count += 1
                else:
                    log.warning("Пропуск '%s': базовые чаты (tl.Chat) не могут быть присоединены через JoinChannelRequest.", group.title)
            except FloodWaitError as e:
                wait_sec = int(e.seconds) + 5
                log.warning("FloodWait: необходимо подождать %d сек перед продолжением.", wait_sec)
                await asyncio.sleep(wait_sec)
                # Попробуем еще раз после ожидания
                try:
                    if isinstance(group, tl.Channel):
                        await client(JoinChannelRequest(group))
                        log.info("Успешно вступили в '%s' (после FloodWait)", group.title)
                        joined_count += 1
                except Exception as ex:
                    log.error("Не удалось вступить в '%s' после FloodWait: %s", group.title, repr(ex))
            except (ChannelsTooMuchError, UsersTooMuchError) as e:
                log.error("Лимит на аккаунте превышен! Не удалось вступить в '%s': %s", group.title, type(e).__name__)
                break
            except Exception as e:
                log.error("Не удалось вступить в '%s': %s", group.title, repr(e))

            # Если это не последний элемент, делаем паузу
            if idx < len(to_join):
                delay = random.randint(min(args.delay_min, args.delay_max), max(args.delay_min, args.delay_max))
                log.info("Пауза перед следующим вступлением: %d сек...", delay)
                await asyncio.sleep(delay)

        log.info("Процесс завершен. Успешно вступили в %d групп из %d.", joined_count, len(to_join))

    finally:
        await client.disconnect()


def main() -> None:
    try:
        asyncio.run(run_search())
    except KeyboardInterrupt:
        log.warning("Поиск прерван пользователем.")


if __name__ == "__main__":
    main()
