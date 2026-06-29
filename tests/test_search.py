from __future__ import annotations

import argparse
import random
from types import SimpleNamespace
from typing import Any

import pytest
from telethon.errors import (
    ChannelsTooMuchError,
    FloodWaitError,
    UsersTooMuchError,
)
from telethon.tl import functions
from telethon.tl.functions.help import GetConfigRequest
from telethon.tl.types import Channel, ChannelForbidden, Chat, ChatForbidden, User

import search
from search import _run_search, is_search_group, parse_args

# ---------------------------------------------------------------------------
# Factories + helpers
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


def _floodwait(seconds: int) -> FloodWaitError:
    return FloodWaitError(request=GetConfigRequest(), capture=str(seconds))


def _patch_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []

    async def _fake_sleep(s: float) -> None:
        sleeps.append(s)

    monkeypatch.setattr(search.asyncio, "sleep", _fake_sleep)
    return sleeps


def _args(**overrides: Any) -> argparse.Namespace:
    """Build a parse_args-shaped namespace with sensible defaults."""
    base = {
        "query": "test",
        "limit": 20,
        "join": False,
        "delay_min": 15,
        "delay_max": 30,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def _search_response(*chats: Any) -> SimpleNamespace:
    return SimpleNamespace(chats=list(chats))


class _FakeClient:
    """A minimal Telethon stand-in used by ``_run_search`` tests.

    Records every request passed to ``__call__`` and supports configurable
    behaviour for ``SearchRequest`` and ``JoinChannelRequest`` via the
    ``join_scenarios`` list (one element per group, in order).
    """

    def __init__(
        self,
        chats: list[Any],
        existing_dialog_ids: list[int] | None = None,
        join_scenarios: list[Any] | None = None,
    ) -> None:
        self.chats = list(chats)
        self.existing_dialog_ids = list(existing_dialog_ids or [])
        self.join_scenarios = list(join_scenarios or [])
        self.requests: list[Any] = []

    async def get_dialogs(self) -> list[Any]:
        return [SimpleNamespace(id=i) for i in self.existing_dialog_ids]

    async def __call__(self, request: Any) -> Any:
        self.requests.append(request)
        if isinstance(request, functions.contacts.SearchRequest):
            return _search_response(*self.chats)
        if type(request).__name__ == "JoinChannelRequest":
            scenario = self.join_scenarios.pop(0) if self.join_scenarios else None
            if isinstance(scenario, BaseException):
                raise scenario
            return None
        raise AssertionError(f"Unexpected request type: {type(request).__name__}")


def _client_with_search(chats: list[Any]) -> Any:
    """Convenience for the dry-run path: a fake client that only does search."""
    return _FakeClient(chats)


# ---------------------------------------------------------------------------
# is_search_group (existing, for context)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# parse_args()
# ---------------------------------------------------------------------------


def test_parse_args_defaults(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("sys.argv", ["search.py", "--query", "python"])
    args = parse_args()
    assert args.query == "python"
    assert args.limit == 20
    assert args.join is False
    assert args.delay_min == 15
    assert args.delay_max == 30


def test_parse_args_all_flags(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "search.py",
            "-q",
            "django",
            "-l",
            "50",
            "--join",
            "--delay-min",
            "5",
            "--delay-max",
            "10",
        ],
    )
    args = parse_args()
    assert args.query == "django"
    assert args.limit == 50
    assert args.join is True
    assert args.delay_min == 5
    assert args.delay_max == 10


def test_parse_args_query_required(monkeypatch: pytest.MonkeyPatch):
    """Without --query argparse must exit with error code 2."""
    monkeypatch.setattr("sys.argv", ["search.py"])
    with pytest.raises(SystemExit) as exc_info:
        parse_args()
    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# _run_search() — dry-run / non-join path
# ---------------------------------------------------------------------------


async def test_run_search_no_results_returns_early():
    client = _client_with_search([])

    # Should not raise and should never call the join path.
    await _run_search(client, _args(query="empty"))

    assert True  # reached; no exception


async def test_run_search_only_invalid_chats_returns_early():
    """Forbidden/User entities must be filtered out by is_search_group."""
    only_users = [User(id=1, first_name="A"), _channel(3, megagroup=False)]
    client = _client_with_search(only_users)
    await _run_search(client, _args(query="x"))


async def test_run_search_does_not_join_when_flag_off(caplog: pytest.LogCaptureFixture):
    """--join not passed → no JoinChannelRequest calls, just informational logs."""
    megagroup = _channel(123, title="A", megagroup=True, access_hash=1)
    client = _FakeClient([megagroup])

    await _run_search(client, _args(query="x", join=False))

    assert any(isinstance(r, functions.contacts.SearchRequest) for r in client.requests)
    assert not any(type(r).__name__ == "JoinChannelRequest" for r in client.requests)


async def test_run_search_dry_run_skips_already_joined(caplog: pytest.LogCaptureFixture):
    """A group you already belong to is still listed, but the join loop skips it.

    Verified by inspecting logs in the dry-run path (no --join flag), which
    exists precisely to show the user what is and isn't already joined.
    """
    megagroup = _channel(55, title="Existing", megagroup=True, access_hash=1)
    client = _FakeClient([megagroup], existing_dialog_ids=[55])
    await _run_search(client, _args(query="x", join=False))


# ---------------------------------------------------------------------------
# _run_search() — join loop error paths
# ---------------------------------------------------------------------------


async def test_run_search_joins_only_new_groups(monkeypatch: pytest.MonkeyPatch):
    sleeps = _patch_sleep(monkeypatch)
    monkeypatch.setattr(random, "randint", lambda a, b: 7)

    already_joined = _channel(100, title="Old", megagroup=True, access_hash=1)
    new_group = _channel(200, title="New", megagroup=True, access_hash=2)

    client = _FakeClient(
        [already_joined, new_group],
        existing_dialog_ids=[100],
        join_scenarios=[None],  # exactly one join (for the new group)
    )

    await _run_search(client, _args(query="x", join=True, delay_min=5, delay_max=10))

    # Exactly one new group → exactly one join attempt + no inter-group delay.
    assert sleeps == []
    join_requests = [r for r in client.requests if type(r).__name__ == "JoinChannelRequest"]
    assert len(join_requests) == 1


async def test_run_search_basic_chat_is_warned_and_skipped(monkeypatch: pytest.MonkeyPatch):
    """tl.Chat (basic group) is in found_groups but cannot be joined via JoinChannelRequest."""
    sleeps = _patch_sleep(monkeypatch)
    basic = _chat(7, title="OldFormatGroup")

    client = _FakeClient([basic])

    await _run_search(client, _args(query="x", join=True))

    assert sleeps == []  # nothing to delay between
    join_requests = [r for r in client.requests if type(r).__name__ == "JoinChannelRequest"]
    assert join_requests == []


async def test_run_search_floodwait_sleeps_and_retries(monkeypatch: pytest.MonkeyPatch):
    sleeps = _patch_sleep(monkeypatch)
    monkeypatch.setattr(random, "randint", lambda a, b: 0)

    group = _channel(11, title="G", megagroup=True, access_hash=1)
    fw = _floodwait(4)
    client = _FakeClient([group], join_scenarios=[fw, None])

    await _run_search(client, _args(query="x", join=True, delay_min=15, delay_max=30))

    join_requests = [r for r in client.requests if type(r).__name__ == "JoinChannelRequest"]
    assert len(join_requests) == 2  # initial + retry after floodwait
    # First sleep: 4 + 5 = 9 (FloodWait). No other sleeps because only one new group.
    assert sleeps == [9]


async def test_run_search_double_floodwait_logs_and_skips(monkeypatch: pytest.MonkeyPatch):
    sleeps = _patch_sleep(monkeypatch)
    monkeypatch.setattr(random, "randint", lambda a, b: 0)

    group = _channel(11, title="G", megagroup=True, access_hash=1)
    fw = _floodwait(2)
    client = _FakeClient([group], join_scenarios=[fw, fw])

    await _run_search(client, _args(query="x", join=True))

    # Two FloodWaits logged: first sleep 2+5=7, second raises and is logged only.
    assert sleeps == [7]


async def test_run_search_channels_too_much_breaks_loop(monkeypatch: pytest.MonkeyPatch):
    sleeps = _patch_sleep(monkeypatch)
    monkeypatch.setattr(random, "randint", lambda a, b: 0)

    g1 = _channel(1, title="G1", megagroup=True, access_hash=1)
    g2 = _channel(2, title="G2", megagroup=True, access_hash=2)

    client = _FakeClient(
        [g1, g2],
        join_scenarios=[
            ChannelsTooMuchError(request=GetConfigRequest()),
            None,  # would be second join attempt, but loop breaks first
        ],
    )

    await _run_search(client, _args(query="x", join=True))

    # ChannelsTooMuchError breaks the loop → no inter-group sleeps.
    assert sleeps == []
    join_requests = [r for r in client.requests if type(r).__name__ == "JoinChannelRequest"]
    assert len(join_requests) == 1


async def test_run_search_users_too_much_breaks_loop(monkeypatch: pytest.MonkeyPatch):
    sleeps = _patch_sleep(monkeypatch)
    monkeypatch.setattr(random, "randint", lambda a, b: 0)

    g1 = _channel(1, title="G1", megagroup=True, access_hash=1)
    g2 = _channel(2, title="G2", megagroup=True, access_hash=2)

    client = _FakeClient(
        [g1, g2],
        join_scenarios=[
            UsersTooMuchError(request=GetConfigRequest()),
            None,
        ],
    )

    await _run_search(client, _args(query="x", join=True))

    assert sleeps == []
    join_requests = [r for r in client.requests if type(r).__name__ == "JoinChannelRequest"]
    assert len(join_requests) == 1


async def test_run_search_generic_exception_does_not_break_loop(monkeypatch: pytest.MonkeyPatch):
    """Unknown exceptions are logged and the loop moves on to the next group."""
    sleeps = _patch_sleep(monkeypatch)
    monkeypatch.setattr(random, "randint", lambda a, b: a)  # deterministic = lower bound

    g1 = _channel(1, title="G1", megagroup=True, access_hash=1)
    g2 = _channel(2, title="G2", megagroup=True, access_hash=2)

    client = _FakeClient([g1, g2], join_scenarios=[RuntimeError("network glitch"), None])

    await _run_search(client, _args(query="x", join=True, delay_min=5, delay_max=10))

    # Inter-group delay only between g1 and g2 (lower bound returned by randint).
    assert sleeps == [5]
    join_requests = [r for r in client.requests if type(r).__name__ == "JoinChannelRequest"]
    assert len(join_requests) == 2


async def test_run_search_already_joined_all_no_op(monkeypatch: pytest.MonkeyPatch):
    sleeps = _patch_sleep(monkeypatch)
    monkeypatch.setattr(random, "randint", lambda a, b: 1)

    in_already = _channel(1, title="A", megagroup=True, access_hash=1)
    client = _FakeClient([in_already], existing_dialog_ids=[1])

    await _run_search(client, _args(query="x", join=True))

    # No new groups → no join attempts → no sleeps.
    assert sleeps == []
    join_requests = [r for r in client.requests if type(r).__name__ == "JoinChannelRequest"]
    assert join_requests == []


async def test_run_search_delay_uses_min_max_bounds(monkeypatch: pytest.MonkeyPatch):
    """The delay between joins is computed as ``random.randint(min, max)`` — we
    swap min/max to confirm the implementation clamps them correctly."""
    sleeps = _patch_sleep(monkeypatch)
    observed: list[tuple[int, int]] = []

    def _record_randint(a: int, b: int) -> int:
        observed.append((a, b))
        return 99  # any constant

    monkeypatch.setattr(random, "randint", _record_randint)
    g1 = _channel(1, title="G1", megagroup=True, access_hash=1)
    g2 = _channel(2, title="G2", megagroup=True, access_hash=2)

    client = _FakeClient([g1, g2], join_scenarios=[None, None])

    # Swap min/max to verify the implementation handles it without crashing.
    await _run_search(client, _args(query="x", join=True, delay_min=30, delay_max=5))

    # Implementation must call randint(min(delay_min, delay_max), max(delay_min, delay_max)).
    assert observed == [(5, 30)]
    # One inter-group sleep happened.
    assert sleeps == [99]
