"""Скрипт для поиска публичных групп/супергрупп.

Поддерживает поиск по ключевым словам и опциональное авто-вступление.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random

from telethon import functions
from telethon.errors import ChannelsTooMuchError, FloodWaitError, UsersTooMuchError
from telethon.tl import types as tl
from telethon.tl.functions.channels import JoinChannelRequest

from reklama import auth
from reklama.utils import clean_control_chars, setup_logging

log = logging.getLogger("search")


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


async def _run_search(client, args: argparse.Namespace) -> None:  # noqa: ANN001
    """Основная логика поиска и вступления в группы."""
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

    found_groups = [chat for chat in result.chats if is_search_group(chat)]

    log.info("Всего найдено подходящих групп: %d.", len(found_groups))
    if not found_groups:
        log.info("Групп по запросу не найдено.")
        return

    for idx, group in enumerate(found_groups, start=1):
        username = getattr(group, "username", None)
        username_str = f"@{username}" if username else "нет username"
        participants = getattr(group, "participants_count", None)
        part_str = (
            f"{participants} уч."
            if participants is not None
            else "кол-во участников неизвестно"
        )
        status = "[состоите]" if group.id in joined_ids else "[не состоите]"
        log.info(
            "%d. %s (id=%d, %s, %s, %s)",
            idx,
            clean_control_chars(group.title),
            group.id,
            username_str,
            part_str,
            status,
        )

    if not args.join:
        log.info(
            "Режим вступления отключен. Для автоматического вступления запустите с флагом --join"
        )
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
        log.info(
            "[%d/%d] Вступаем в '%s' (%s)...",
            idx,
            len(to_join),
            clean_control_chars(group.title),
            identifier,
        )

        try:
            if isinstance(group, tl.Channel):
                await client(JoinChannelRequest(group))
                log.info("Успешно вступили в '%s'", clean_control_chars(group.title))
                joined_count += 1
            else:
                log.warning(
                    "Пропуск '%s': базовые чаты (tl.Chat) не могут быть "
                    "присоединены через JoinChannelRequest.",
                    clean_control_chars(group.title),
                )
        except FloodWaitError as e:
            wait_sec = int(e.seconds) + 5
            log.warning("FloodWait: необходимо подождать %d сек перед продолжением.", wait_sec)
            await asyncio.sleep(wait_sec)
            # Попробуем еще раз после ожидания
            try:
                if isinstance(group, tl.Channel):
                    await client(JoinChannelRequest(group))
                    log.info(
                        "Успешно вступили в '%s' (после FloodWait)",
                        clean_control_chars(group.title),
                    )
                    joined_count += 1
            except FloodWaitError:
                log.error(
                    "Повторный FloodWait в '%s'. Пропускаем.",
                    clean_control_chars(group.title),
                )
            except (ChannelsTooMuchError, UsersTooMuchError) as e:
                log.error(
                    "Лимит на аккаунте превышен при повторной попытке! "
                    "Не удалось вступить в '%s': %s",
                    clean_control_chars(group.title),
                    type(e).__name__,
                )
                break
            except Exception as e:  # noqa: BLE001
                log.error(
                    "Не удалось вступить в '%s' после FloodWait: %s",
                    clean_control_chars(group.title),
                    repr(e),
                )
        except (ChannelsTooMuchError, UsersTooMuchError) as e:
            log.error(
                "Лимит на аккаунте превышен! Не удалось вступить в '%s': %s",
                clean_control_chars(group.title),
                type(e).__name__,
            )
            break
        except Exception as e:  # noqa: BLE001
            log.error("Не удалось вступить в '%s': %s", clean_control_chars(group.title), repr(e))

        # Если это не последний элемент, делаем паузу
        if idx < len(to_join):
            delay = random.randint(
                min(args.delay_min, args.delay_max),
                max(args.delay_min, args.delay_max),
            )
            log.info("Пауза перед следующим вступлением: %d сек...", delay)
            await asyncio.sleep(delay)

    log.info("Процесс завершен. Успешно вступили в %d групп из %d.", joined_count, len(to_join))


async def main() -> None:
    args = parse_args()
    log_file = setup_logging("search")
    log.info("Лог поиска: %s", log_file)
    log.info("Поиск по запросу: '%s' (лимит: %d)", args.query, args.limit)

    async with auth.client_session() as client:
        await _run_search(client, args)


def entrypoint() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.warning("Поиск прерван пользователем.")


if __name__ == "__main__":
    entrypoint()
