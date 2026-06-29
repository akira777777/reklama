from __future__ import annotations

from telethon.tl.types import Channel, ChannelForbidden, Chat, ChatForbidden, User

from dialogs import entity_id, entity_title, filter_dialogs, is_group


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
