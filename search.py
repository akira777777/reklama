"""Скрипт для поиска публичных групп/супергрупп.

Поддерживает поиск по ключевым словам и опциональное авто-вступление.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import re

from telethon import functions
from telethon.errors import ChannelsTooMuchError, FloodWaitError, UsersTooMuchError
from telethon.tl import types as tl
from telethon.tl.functions.channels import JoinChannelRequest

from reklama import auth
from reklama.utils import clean_control_chars, setup_logging

log = logging.getLogger("search")


search_state = {
    "running": False,
    "finished": False,
    "joined_count": 0,
    "total_found": 0,
    "current_group": "Нет",
    "status": "Ожидание",
    "timer_remaining": 0.0,
    "timer_total": 0.0,
}


class SearchArgs:
    def __init__(
        self,
        query: str | None = None,
        file: str | None = None,
        links: list[str] | None = None,
        limit: int = 20,
        join: bool = False,
        delay_min: int = 15,
        delay_max: int = 30,
        join_batch_size: int = 5,
        join_batch_delay_min: int = 180,
        join_batch_delay_max: int = 360,
    ):
        self.query = query
        self.file = file
        self.links = links
        self.limit = limit
        self.join = join
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.join_batch_size = join_batch_size
        self.join_batch_delay_min = join_batch_delay_min
        self.join_batch_delay_max = join_batch_delay_max


async def search_sleep(duration: float, status_label: str = "Ожидание") -> None:
    if duration <= 0:
        return
    search_state["status"] = status_label
    search_state["timer_total"] = duration
    search_state["timer_remaining"] = duration
    step = 0.1
    elapsed = 0.0
    while elapsed < duration and search_state["running"]:
        await asyncio.sleep(step)
        elapsed += step
        search_state["timer_remaining"] = max(0.0, duration - elapsed)
    search_state["timer_total"] = 0.0
    search_state["timer_remaining"] = 0.0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Поиск новых публичных групп и каналов в Telegram.",
    )
    p.add_argument(
        "-q",
        "--query",
        type=str,
        help="Ключевое слово для поиска в Telegram.",
    )
    p.add_argument(
        "-f",
        "--file",
        type=str,
        help="Путь к файлу со ссылками или юзернеймами (по одной строке на группу).",
    )
    p.add_argument(
        "-ls",
        "--links",
        type=str,
        nargs="+",
        help="Список ссылок или юзернеймов групп через пробел.",
    )
    p.add_argument(
        "-l",
        "--limit",
        type=int,
        default=20,
        help="Ограничение количества результатов глобального поиска (по умолчанию: 20).",
    )
    p.add_argument(
        "-j",
        "--join",
        action="store_true",
        help="Автоматически вступать во все найденные/указанные группы.",
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
    p.add_argument(
        "--join-batch-size",
        type=int,
        default=5,
        help="Размер пакета вступлений перед длинной паузой (по умолчанию: 5).",
    )
    p.add_argument(
        "--join-batch-delay-min",
        type=int,
        default=180,
        help="Минимальная пауза между пакетами вступлений в секундах (по умолчанию: 180).",
    )
    p.add_argument(
        "--join-batch-delay-max",
        type=int,
        default=360,
        help="Максимальная пауза между пакетами вступлений в секундах (по умолчанию: 360).",
    )
    args = p.parse_args()
    if not (args.query or args.file or args.links):
        p.error("Необходимо указать хотя бы один источник: --query, --file или --links")
    return args


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


def parse_telegram_link(link: str) -> tuple[str, str] | None:
    """Разбирает ссылку/юзернейм Telegram.

    Возвращает:
      - ('hash', hash_str) для инвайт-ссылок
      - ('username', username_str) для публичных групп/юзернеймов
      - None, если не удалось распознать.
    """
    link = link.strip()
    if not link:
        return None
    # Удаляем протоколы, домены и т.д.
    s = re.sub(r"^(https?://)?(www\.)?(t\.me|telegram\.me|telegram\.dog)/", "", link, flags=re.IGNORECASE)
    if not s:
        return None

    if s.startswith("+"):
        invite_hash = s[1:]
        return ("hash", invite_hash) if invite_hash else None

    if s.startswith("joinchat/"):
        invite_hash = s[len("joinchat/"):]
        return ("hash", invite_hash) if invite_hash else None

    if s.startswith("@"):
        s = s[1:]

    # Регулярное выражение для валидного юзернейма
    if re.match(r"^[a-zA-Z0-9_]+$", s):
        return ("username", s)

    return None


async def verify_link(client, link: str, joined_ids: set[int]) -> dict | None:  # noqa: ANN001
    """Проверяет ссылку/юзернейм перед вступлением.

    Возвращает словарь с метаданными о группе или None, если группа не подходит.
    """
    parsed = parse_telegram_link(link)
    if not parsed:
        return {"link": link, "valid": False, "error": "Неверный формат ссылки"}

    ltype, lval = parsed

    if ltype == "username":
        try:
            entity = await client.get_entity(lval)
            if not is_search_group(entity):
                return {
                    "link": link,
                    "valid": False,
                    "error": "Сущность не является группой или супергруппой (возможно, это канал-трансляция или пользователь)",
                }

            joined = entity.id in joined_ids
            title = getattr(entity, "title", "Без названия")
            return {
                "link": link,
                "type": "username",
                "value": lval,
                "title": title,
                "entity": entity,
                "joined": joined,
                "valid": True,
            }
        except Exception as e:
            return {"link": link, "valid": False, "error": f"Не удалось получить информацию о юзернейме: {e}"}

    elif ltype == "hash":
        try:
            from telethon.tl.functions.messages import CheckChatInviteRequest
            from telethon.tl.types import ChatInvite, ChatInviteAlready

            invite = await client(CheckChatInviteRequest(hash=lval))

            if isinstance(invite, ChatInviteAlready):
                chat = invite.chat
                title = getattr(chat, "title", "Без названия")
                return {
                    "link": link,
                    "type": "hash",
                    "value": lval,
                    "title": title,
                    "entity": chat,
                    "joined": True,
                    "valid": True,
                }
            elif isinstance(invite, ChatInvite):
                title = invite.title
                is_broadcast = bool(getattr(invite, "broadcast", False))
                is_megagroup = bool(getattr(invite, "megagroup", False))

                if is_broadcast and not is_megagroup:
                    return {
                        "link": link,
                        "valid": False,
                        "error": "Ссылка ведет на односторонний канал-трансляцию (писать сообщения нельзя)",
                    }

                return {
                    "link": link,
                    "type": "hash",
                    "value": lval,
                    "title": title,
                    "entity": None,
                    "joined": False,
                    "valid": True,
                }
        except Exception as e:
            return {"link": link, "valid": False, "error": f"Не удалось проверить пригласительную ссылку: {e}"}

    return None


async def _run_search(client, args: argparse.Namespace) -> None:  # noqa: ANN001
    """Основная логика поиска и вступления в группы."""
    search_state["running"] = True
    search_state["joined_count"] = 0
    search_state["total_found"] = 0
    search_state["current_group"] = "Нет"
    search_state["status"] = "Загрузка диалогов..."

    try:
        # 1. Получаем список диалогов, в которых мы уже состоим
        log.info("Загрузка ваших текущих диалогов...")
        dialogs = await client.get_dialogs()
        joined_ids = {d.id for d in dialogs}
        log.info("Вы состоите в %d диалогах.", len(joined_ids))
        if len(joined_ids) >= 485:
            log.warning(
                "ВНИМАНИЕ: Вы состоите в %d диалогах (лимит Telegram — 500). "
                "Дальнейшее вступление может привести к ошибкам лимита аккаунта.",
                len(joined_ids),
            )

        if not search_state["running"]:
            return

        search_state["status"] = "Сбор целевых групп..."
        # 2. Собираем список групп для вступления/проверки
        targets: list[dict] = []

        # 2а. Обрабатываем --query (глобальный поиск)
        if args.query:
            log.info("Выполняем глобальный поиск по запросу: '%s'...", args.query)
            try:
                result = await client(
                    functions.contacts.SearchRequest(
                        q=args.query,
                        limit=args.limit,
                    )
                )
                found_groups = [chat for chat in result.chats if is_search_group(chat)]
                for group in found_groups:
                    username = getattr(group, "username", None)
                    identifier = f"@{username}" if username else f"id={group.id}"
                    targets.append({
                        "type": "search_entity",
                        "link_or_id": identifier,
                        "title": getattr(group, "title", "Без названия"),
                        "entity": group,
                        "hash": None,
                        "joined": group.id in joined_ids,
                        "valid": True,
                        "error": None,
                    })
            except Exception as e:
                log.error("Ошибка при выполнении глобального поиска: %s", e)

        if not search_state["running"]:
            return

        # 2б. Обрабатываем --file
        if args.file:
            from pathlib import Path
            path = Path(args.file)
            if not path.is_file():
                log.error("Файл со ссылками не найден: %s", path)
            else:
                log.info("Читаем ссылки из файла: %s...", path)
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                    links = [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]
                    log.info("Найдено %d строк со ссылками.", len(links))
                    search_state["status"] = f"Проверка ссылок из файла (0/{len(links)})..."
                    for link_idx, link in enumerate(links, start=1):
                        if not search_state["running"]:
                            return
                        search_state["status"] = f"Проверка ссылок из файла ({link_idx}/{len(links)})..."
                        res = await verify_link(client, link, joined_ids)
                        if res:
                            if res.get("valid"):
                                targets.append({
                                    "type": res["type"],
                                    "link_or_id": link,
                                    "title": res["title"],
                                    "entity": res.get("entity"),
                                    "hash": res["value"] if res["type"] == "hash" else None,
                                    "joined": res["joined"],
                                    "valid": True,
                                    "error": None,
                                })
                                status_str = "[состоите]" if res["joined"] else "[не состоите]"
                                log.info("  -> Успешно проверено: '%s' (%s)", res["title"], status_str)
                            else:
                                log.warning("  -> Не подходит: %s", res.get("error", "Неизвестная ошибка"))
                except Exception as e:
                    log.error("Ошибка при чтении файла со ссылками: %s", e)

        if not search_state["running"]:
            return

        # 2в. Обрабатываем --links
        if args.links:
            log.info("Проверяем список переданных ссылок (%d шт.)...", len(args.links))
            search_state["status"] = f"Проверка переданных ссылок (0/{len(args.links)})..."
            for link_idx, link in enumerate(args.links, start=1):
                if not search_state["running"]:
                    return
                search_state["status"] = f"Проверка переданных ссылок ({link_idx}/{len(args.links)})..."
                res = await verify_link(client, link, joined_ids)
                if res:
                    if res.get("valid"):
                        targets.append({
                            "type": res["type"],
                            "link_or_id": link,
                            "title": res["title"],
                            "entity": res.get("entity"),
                            "hash": res["value"] if res["type"] == "hash" else None,
                            "joined": res["joined"],
                            "valid": True,
                            "error": None,
                        })
                        status_str = "[состоите]" if res["joined"] else "[не состоите]"
                        log.info("  -> Успешно проверено: '%s' (%s)", res["title"], status_str)
                    else:
                        log.warning("  -> Не подходит: %s", res.get("error", "Неизвестная ошибка"))

        # Отчет по найденным/проверенным группам
        log.info("Всего подходящих групп найдено/проверено: %d", len(targets))
        if not targets:
            log.info("Нет подходящих групп для обработки.")
            search_state["status"] = "Завершено (нет групп)"
            return

        # Выводим список
        log.info("Список групп:")
        for idx, t in enumerate(targets, start=1):
            status = "[состоите]" if t["joined"] else "[не состоите]"
            log.info("%d. %s (%s, %s)", idx, clean_control_chars(t["title"]), t["link_or_id"], status)

        if not args.join:
            log.info("Режим вступления отключен. Для автоматического вступления запустите с флагом --join")
            search_state["status"] = "Завершено (без вступления)"
            return

        # Отбираем только те, в которых мы не состоим
        to_join = [t for t in targets if not t["joined"]]
        search_state["total_found"] = len(to_join)
        if not to_join:
            log.info("Вы уже состоите во всех подходящих группах.")
            search_state["status"] = "Завершено (уже состоите)"
            return

        log.info("Начинаем вступление в группы (всего к вступлению: %d)...", len(to_join))
        joined_count = 0

        for idx, t in enumerate(to_join, start=1):
            if not search_state["running"]:
                log.warning("Процесс вступления прерван пользователем.")
                break

            search_state["current_group"] = t["title"]
            search_state["status"] = f"Вступление ({idx}/{len(to_join)})"

            log.info(
                "[%d/%d] Вступаем в '%s' (%s)...",
                idx,
                len(to_join),
                clean_control_chars(t["title"]),
                t["link_or_id"],
            )

            try:
                if t["type"] in ("username", "search_entity"):
                    entity = t["entity"]
                    if isinstance(entity, tl.Channel):
                        await client(JoinChannelRequest(entity))
                        log.info("Успешно вступили в '%s'", clean_control_chars(t["title"]))
                        joined_count += 1
                        search_state["joined_count"] = joined_count
                    else:
                        log.warning(
                            "Пропуск '%s': базовые чаты (tl.Chat) не могут быть "
                            "присоединены через JoinChannelRequest.",
                            clean_control_chars(t["title"]),
                        )
                elif t["type"] == "hash":
                    from telethon.tl.functions.messages import ImportChatInviteRequest
                    await client(ImportChatInviteRequest(hash=t["hash"]))
                    log.info("Успешно вступили в '%s'", clean_control_chars(t["title"]))
                    joined_count += 1
                    search_state["joined_count"] = joined_count

            except FloodWaitError as e:
                wait_sec = int(e.seconds) + 5
                log.warning("FloodWait: необходимо подождать %d сек перед продолжением.", wait_sec)
                await search_sleep(wait_sec, "Ожидание лимитов (FloodWait)")
                if not search_state["running"]:
                    break
                # Попробуем еще раз после ожидания
                try:
                    if t["type"] in ("username", "search_entity"):
                        await client(JoinChannelRequest(t["entity"]))
                    elif t["type"] == "hash":
                        from telethon.tl.functions.messages import ImportChatInviteRequest
                        await client(ImportChatInviteRequest(hash=t["hash"]))
                    log.info("Успешно вступили в '%s' (после FloodWait)", clean_control_chars(t["title"]))
                    joined_count += 1
                    search_state["joined_count"] = joined_count
                except FloodWaitError:
                    log.error("Повторный FloodWait в '%s'. Пропускаем.", clean_control_chars(t["title"]))
                except (ChannelsTooMuchError, UsersTooMuchError) as e:
                    log.error(
                        "Лимит на аккаунте превышен при повторной попытке! Не удалось вступить в '%s': %s",
                        clean_control_chars(t["title"]),
                        type(e).__name__,
                    )
                    break
                except Exception as e:
                    log.error("Не удалось вступить в '%s' после FloodWait: %s", clean_control_chars(t["title"]), repr(e))

            except (ChannelsTooMuchError, UsersTooMuchError) as e:
                log.error(
                    "Лимит на аккаунте превышен! Не удалось вступить в '%s': %s",
                    clean_control_chars(t["title"]),
                    type(e).__name__,
                )
                break
            except Exception as e:
                log.error("Не удалось вступить в '%s': %s", clean_control_chars(t["title"]), repr(e))

            if idx < len(to_join):
                if joined_count > 0 and joined_count % args.join_batch_size == 0:
                    batch_delay = random.randint(
                        min(args.join_batch_delay_min, args.join_batch_delay_max),
                        max(args.join_batch_delay_min, args.join_batch_delay_max),
                    )
                    log.info(
                        "Пауза пакета вступлений (каждые %d вступлений): %d сек...",
                        args.join_batch_size,
                        batch_delay,
                    )
                    await search_sleep(batch_delay, f"Пауза пакета вступлений ({args.join_batch_size})")
                else:
                    delay = random.randint(
                        min(args.delay_min, args.delay_max),
                        max(args.delay_min, args.delay_max),
                    )
                    log.info("Пауза перед следующим вступлением: %d сек...", delay)
                    await search_sleep(delay, "Пауза перед вступлением")

        log.info("Процесс завершен. Успешно вступили в %d групп из %d.", joined_count, len(to_join))
        search_state["status"] = "Завершено"
    finally:
        search_state["running"] = False
        search_state["finished"] = True


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
