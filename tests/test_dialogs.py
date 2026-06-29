"""Юнит-тесты фильтрации диалогов (без сети, на реальных TLObject-типах Telethon)."""

from __future__ import annotations

from telethon.tl import types as tl

import dialogs


def _chat(id_: int, title: str = "g", **kw: object) -> tl.Chat:
    base: dict[str, object] = {
        "id": id_,
        "title": title,
        "photo": None,
        "participants_count": 10,
        "date": None,
        "version": 0,
    }
    base.update(kw)
    return tl.Chat(**base)  # type: ignore[arg-type]


def _channel(id_: int, title: str = "c", **kw: object) -> tl.Channel:
    base: dict[str, object] = {"id": id_, "title": title, "photo": None, "date": None}
    base.update(kw)
    return tl.Channel(**base)  # type: ignore[arg-type]


def test_basic_chat_is_group() -> None:
    assert dialogs.is_group(_chat(1, "базовая группа")) is True


def test_megagroup_is_group() -> None:
    assert dialogs.is_group(_channel(2, "супергруппа", megagroup=True, broadcast=False)) is True


def test_broadcast_channel_is_not_group() -> None:
    assert dialogs.is_group(_channel(3, "канал", megagroup=False, broadcast=True)) is False


def test_left_megagroup_is_not_group() -> None:
    ch = _channel(4, "покинутая", megagroup=True, broadcast=False, left=True)
    assert dialogs.is_group(ch) is False


def test_user_is_not_group() -> None:
    assert dialogs.is_group(tl.User(id=5, first_name="Иван")) is False


def test_chat_empty_is_not_group() -> None:
    assert dialogs.is_group(tl.ChatEmpty(id=6)) is False


def test_chat_forbidden_is_not_group() -> None:
    assert dialogs.is_group(tl.ChatForbidden(id=7, title="запр")) is False


def test_channel_forbidden_is_not_group() -> None:
    cf = tl.ChannelForbidden(id=8, access_hash=0, title="запр", megagroup=True)
    assert dialogs.is_group(cf) is False


def test_filter_dialogs_keeps_only_groups() -> None:
    items = [
        _chat(1, "g1"),
        tl.User(id=2, first_name="u"),
        _channel(3, "mega", megagroup=True),
        _channel(4, "broadcast", broadcast=True),
        tl.ChatEmpty(id=5),
    ]
    kept = dialogs.filter_dialogs(items)
    kept_ids = [dialogs.entity_id(e) for e in kept]
    assert kept_ids == [1, 3]


def test_entity_title_falls_back_to_id() -> None:
    assert dialogs.entity_title(tl.ChatEmpty(id=42)) == "id=42"
    assert dialogs.entity_title(_chat(7, "имя")) == "имя"
