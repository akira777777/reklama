from __future__ import annotations

from telethon.tl.types import Channel, ChannelForbidden, Chat, ChatForbidden, User

from search import is_search_group


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


def test_is_search_group_chat():
    chat = _chat(1)
    assert is_search_group(chat) is True


def test_is_search_group_megagroup():
    channel = _channel(2, megagroup=True)
    assert is_search_group(channel) is True


def test_is_search_group_broadcast_channel():
    channel = _channel(3, megagroup=False)
    assert is_search_group(channel) is False


def test_is_search_group_chat_forbidden():
    forbidden = _chat_forbidden(4)
    assert is_search_group(forbidden) is False


def test_is_search_group_channel_forbidden():
    forbidden = _channel_forbidden(5)
    assert is_search_group(forbidden) is False


def test_is_search_group_user():
    user = User(id=6, first_name="Alice")
    assert is_search_group(user) is False
