from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from telethon.tl.types import Channel, ChannelForbidden, Chat, ChatForbidden, User

from reklama.dialogs import (
    collect_groups,
    entity_id,
    entity_title,
    filter_dialogs,
    is_group,
)

# ---------------------------------------------------------------------------
# Factories (re-used from existing tests; included for self-containment)
# ---------------------------------------------------------------------------


def _chat(id_: int, title: str = "g", **kw: object) -> Chat:
    base: dict[str, object] = {
        "id": id_,
        "title": title,
        "photo": None,
        "participants_count": 10,
        "date": None,
        "version": 0,
    }
    base.update(kw)
    return Chat(**base)  # type: ignore[arg-type]


def _channel(id_: int, title: str = "c", **kw: object) -> Channel:
    base: dict[str, object] = {
        "id": id_,
        "title": title,
        "photo": None,
        "date": None,
    }
    base.update(kw)
    return Channel(**base)  # type: ignore[arg-type]


def _chat_forbidden(id_: int, title: str = "g") -> ChatForbidden:
    return ChatForbidden(id=id_, title=title)


def _channel_forbidden(id_: int, title: str = "c", access_hash: int = 0) -> ChannelForbidden:
    return ChannelForbidden(id=id_, access_hash=access_hash, title=title)


# ---------------------------------------------------------------------------
# is_group / filter_dialogs / entity_id / entity_title (existing)
# ---------------------------------------------------------------------------


def test_is_group_with_basic_chat():
    chat = _chat(id_=123, title="Test Group")
    assert is_group(chat) is True


def test_is_group_with_megagroup():
    channel = _channel(id_=456, title="Supergroup", megagroup=True)
    assert is_group(channel) is True


def test_is_group_with_broadcast_channel():
    channel = _channel(id_=789, title="News", megagroup=False, broadcast=True)
    assert is_group(channel) is False


def test_is_group_with_left_channel():
    channel = _channel(id_=101, title="Left Group", megagroup=True, left=True)
    assert is_group(channel) is False


def test_is_group_with_forbidden_chat():
    forbidden = _chat_forbidden(id_=202)
    assert is_group(forbidden) is False


def test_is_group_with_forbidden_channel():
    forbidden = _channel_forbidden(id_=303)
    assert is_group(forbidden) is False


def test_is_group_with_user():
    user = User(id=404, first_name="John")
    assert is_group(user) is False


def test_filter_dialogs():
    chat = _chat(id_=1, title="Group")
    channel = _channel(id_=2, title="Channel", broadcast=True)
    megagroup = _channel(id_=3, title="Supergroup", megagroup=True)
    forbidden = _chat_forbidden(id_=4)
    dialogs = [chat, channel, megagroup, forbidden]
    result = filter_dialogs(dialogs)
    assert len(result) == 2
    assert chat in result
    assert megagroup in result


def test_entity_id():
    chat = _chat(id_=999, title="Test")
    assert entity_id(chat) == 999


def test_entity_id_default():
    obj = object()
    assert entity_id(obj) == 0


def test_entity_title():
    chat = _chat(id_=1, title="My Group")
    assert entity_title(chat) == "My Group"


def test_entity_title_fallback():
    user = User(id=42, first_name="Alice")
    assert entity_title(user) == "id=42"


def test_entity_title_no_title():
    obj = object()
    assert entity_title(obj) == "id=0"


def test_entity_title_empty_string_falls_back_to_id():
    """An entity whose .title is an empty string must NOT be displayed verbatim."""
    chat = _chat(id_=77, title="")
    assert entity_title(chat) == "id=77"


# ---------------------------------------------------------------------------
# collect_groups()
# ---------------------------------------------------------------------------


class _FakeDialogIterClient:
    """Async-iterable Telethon stand-in used by collect_groups."""

    def __init__(self, dialogs: list[Any]) -> None:
        self._dialogs = dialogs

    def __aiter__(self) -> _FakeDialogIterClient:
        return self

    async def __anext__(self) -> Any:
        if not self._dialogs:
            raise StopAsyncIteration
        return self._dialogs.pop(0)

    async def iter_dialogs(self) -> Any:
        for d in list(self._dialogs):
            yield d


async def test_collect_groups_returns_groups_with_titles_and_entities():
    g1 = _chat(id_=1, title="Group One")
    g2 = _channel(id_=2, title="Group Two", megagroup=True)
    broadcast = _channel(id_=3, title="Channel", broadcast=True)
    user = User(id=4, first_name="Alice")

    client = _FakeDialogIterClient(
        [
            SimpleNamespace(entity=g1),
            SimpleNamespace(entity=broadcast),
            SimpleNamespace(entity=g2),
            SimpleNamespace(entity=user),
        ]
    )

    groups = await collect_groups(client)

    assert len(groups) == 2
    ids = {g[0] for g in groups}
    assert ids == {1, 2}
    titles = {g[1] for g in groups}
    assert titles == {"Group One", "Group Two"}


async def test_collect_groups_dedupes_by_id():
    """The same logical group surfaced via multiple dialogs → one entry."""
    g = _chat(id_=99, title="Same Group")
    client = _FakeDialogIterClient(
        [
            SimpleNamespace(entity=g),
            SimpleNamespace(entity=g),
            SimpleNamespace(entity=g),
        ]
    )

    groups = await collect_groups(client)

    assert len(groups) == 1
    assert groups[0][0] == 99


async def test_collect_groups_empty_returns_empty_list():
    client = _FakeDialogIterClient([])
    groups = await collect_groups(client)
    assert groups == []


async def test_collect_groups_skips_left_and_forbidden():
    """Left megagroups and forbidden chats must be filtered out."""
    left_group = _channel(id_=1, title="Left", megagroup=True, left=True)
    forbidden = _chat_forbidden(id_=2)
    valid = _chat(id_=3, title="Valid")
    client = _FakeDialogIterClient(
        [
            SimpleNamespace(entity=left_group),
            SimpleNamespace(entity=forbidden),
            SimpleNamespace(entity=valid),
        ]
    )

    groups = await collect_groups(client)

    assert len(groups) == 1
    assert groups[0][0] == 3


async def test_collect_groups_entity_ref_passthrough():
    """The 3rd tuple element should be the exact same entity object."""
    ent = _chat(id_=5, title="Entity")
    client = _FakeDialogIterClient([SimpleNamespace(entity=ent)])

    groups = await collect_groups(client)

    assert len(groups) == 1
    assert groups[0][2] is ent
