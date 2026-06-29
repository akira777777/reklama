"""Перебор диалогов и фильтрация: только группы/супергруппы, куда можно писать.

Чистые функции (`is_group`, `filter_dialogs`) тестируются без сети.
Сетевая часть (`collect_groups`) отделена.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from telethon.tl import types as tl

log = logging.getLogger(__name__)

__all__ = ["is_group", "filter_dialogs", "entity_id", "entity_title", "collect_groups"]


def is_group(entity: Any) -> bool:
    """True для базовых групп и супергрупп (megagroup).

    Исключаются: ЛС, каналы-трансляции (broadcast), удалённые/недоступные чаты
    (ChatForbidden/ChannelForbidden), покинутые супергруппы (left=True).
    """
    if isinstance(entity, tl.ChatForbidden | tl.ChannelForbidden):
        return False
    if isinstance(entity, tl.Chat):  # базовая группа
        return True
    # Супергруппа (megagroup=True); каналы-трансляции и покинутые отбрасываем.
    return (
        isinstance(entity, tl.Channel)
        and getattr(entity, "megagroup", False)
        and not getattr(entity, "left", False)
    )


def filter_dialogs(dialogs: Iterable[Any]) -> list[Any]:
    """Оставляет только групповые сущности (применяет is_group)."""
    return [d for d in dialogs if is_group(d)]


def entity_id(entity: Any) -> int:
    """Id сущности (Chat/Channel)."""
    return int(getattr(entity, "id", 0))


def entity_title(entity: Any) -> str:
    """Название группы или запасная строка."""
    title = getattr(entity, "title", None)
    return title if isinstance(title, str) and title else f"id={entity_id(entity)}"


async def collect_groups(client: Any) -> list[tuple[int, str, Any]]:
    """Перебирает диалоги аккаунта и возвращает [(id, title, entity), ...] для групп."""
    groups: list[tuple[int, str, Any]] = []
    seen: set[int] = set()
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if not is_group(entity):
            continue
        eid = entity_id(entity)
        if eid in seen:
            continue
        seen.add(eid)
        groups.append((eid, entity_title(entity), entity))
    log.info("Найдено групп/супергрупп: %d.", len(groups))
    return groups
