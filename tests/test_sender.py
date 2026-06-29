from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from telethon.errors import (
    ChannelPrivateError,
    ChatWriteForbiddenError,
    FloodWaitError,
    SlowModeWaitError,
    UserBannedInChannelError,
)
from telethon.tl.functions.help import GetConfigRequest

from reklama import config, progress, sender
from reklama.sender import SendResult, detect_media_kind, send

# ---------------------------------------------------------------------------
# Sync helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_sender_sleep(monkeypatch: pytest.MonkeyPatch):
    async def _fake_sleep(seconds: float) -> None:
        pass
    monkeypatch.setattr(sender.asyncio, "sleep", _fake_sleep)


def test_send_result_ok():
    assert SendResult(progress.STATUS_SENT).ok is True
    assert SendResult(progress.STATUS_SKIPPED).ok is False
    assert SendResult(progress.STATUS_ERROR).ok is False


def test_detect_media_kind_photo(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "FORCE_DOCUMENT", False)
    assert detect_media_kind("image.png") == "photo"
    assert detect_media_kind("image.jpg") == "photo"


def test_detect_media_kind_video(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "FORCE_DOCUMENT", False)
    assert detect_media_kind("movie.mp4") == "video"


def test_detect_media_kind_document(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "FORCE_DOCUMENT", False)
    assert detect_media_kind("archive.zip") == "document"
    assert detect_media_kind("unknown_ext") == "document"


def test_detect_media_kind_force_document(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "FORCE_DOCUMENT", True)
    assert detect_media_kind("image.png") == "document"
    assert detect_media_kind("movie.mp4") == "document"


def test_skipped_errors_includes_expected_types():
    """Send-loop must skip on these three types — lock the public contract."""
    from reklama.sender import SKIPPED_ERRORS

    assert ChatWriteForbiddenError in SKIPPED_ERRORS
    assert UserBannedInChannelError in SKIPPED_ERRORS
    assert ChannelPrivateError in SKIPPED_ERRORS
    assert SlowModeWaitError not in SKIPPED_ERRORS


# ---------------------------------------------------------------------------
# Async helpers for the send() loop
# ---------------------------------------------------------------------------


def _floodwait(seconds: int) -> FloodWaitError:
    return FloodWaitError(request=GetConfigRequest(), capture=str(seconds))


def _patch_sleep(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Replace asyncio.sleep inside sender with an instant stub; record durations."""
    sleeps: list[int] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(int(seconds))

    monkeypatch.setattr(sender.asyncio, "sleep", _fake_sleep)
    return sleeps


def _client(
    *,
    send_file: Any = None,
    send_message: Any = None,
) -> Any:
    """Build a duck-typed TelegramClient substitute; default to no-op AsyncMocks."""
    return SimpleNamespace(
        send_file=send_file or AsyncMock(),
        send_message=send_message or AsyncMock(),
    )


# ---------------------------------------------------------------------------
# send(): happy paths
# ---------------------------------------------------------------------------


async def test_send_text_only_succeeds():
    msg = AsyncMock()
    client = _client(send_message=msg)

    r = await send(client, "chat-1", "hello", media_path=None)

    assert r.ok is True
    assert r.status == progress.STATUS_SENT
    assert r.reason == "text_only"
    msg.assert_awaited_once_with("chat-1", "hello", formatting_entities=None)


async def test_send_with_media_succeeds(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "FORCE_DOCUMENT", False)
    client = _client(send_file=AsyncMock())

    r = await send(client, "chat-2", "caption", media_path="photo.png")

    assert r.ok is True
    assert r.status == progress.STATUS_SENT
    assert r.reason == "with_media"
    client.send_file.assert_awaited_once()
    kwargs = client.send_file.await_args.kwargs
    assert kwargs["caption"] == "caption"
    assert kwargs["force_document"] is False  # .png → photo
    assert kwargs["formatting_entities"] is None


async def test_send_forwards_formatting_entities():
    msg = AsyncMock()
    client = _client(send_message=msg)
    ents = [object()]

    r = await send(client, "chat-3", "hello", media_path=None, formatting_entities=ents)

    assert r.ok is True
    msg.assert_awaited_once_with("chat-3", "hello", formatting_entities=ents)


# ---------------------------------------------------------------------------
# send(): media failure → text fallback
# ---------------------------------------------------------------------------


async def test_send_media_failure_falls_back_to_text(caplog: pytest.LogCaptureFixture):
    media_exc = RuntimeError("bad photo")
    client = _client(
        send_file=AsyncMock(side_effect=media_exc),
        send_message=AsyncMock(),
    )

    r = await send(client, "chat-4", "caption", media_path="photo.png")

    assert r.ok is True
    assert r.status == progress.STATUS_SENT
    assert r.reason == "text_fallback: RuntimeError"
    client.send_message.assert_awaited_once_with("chat-4", "caption", formatting_entities=None)


async def test_send_media_skipped_error_propagates_without_fallback():
    """SKIPPED_ERRORS from media must surface as 'skipped' and NOT silently fallback."""
    client = _client(
        send_file=AsyncMock(side_effect=ChatWriteForbiddenError(request=GetConfigRequest())),
        send_message=AsyncMock(),
    )

    r = await send(client, "chat-5", "caption", media_path="photo.png")

    assert r.status == progress.STATUS_SKIPPED
    assert r.reason == "ChatWriteForbiddenError"
    client.send_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# send(): FloodWait retry loop
# ---------------------------------------------------------------------------


async def test_send_floodwait_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch):
    sleeps = _patch_sleep(monkeypatch)
    fw = _floodwait(10)
    msg = AsyncMock(side_effect=[fw, None])
    client = _client(send_message=msg)

    r = await send(client, "chat-6", "hello", media_path=None)

    assert r.ok is True
    assert r.reason == "text_only_after_floodwait"
    assert sleeps == [15]  # 10 + 5


async def test_send_floodwait_retries_multiple_times(monkeypatch: pytest.MonkeyPatch):
    sleeps = _patch_sleep(monkeypatch)
    fw = _floodwait(3)
    msg = AsyncMock(side_effect=[fw, fw, fw, None])
    client = _client(send_message=msg)

    r = await send(client, "chat-7", "hi", media_path=None)

    assert r.ok is True
    assert r.reason == "text_only_after_floodwait"
    assert sleeps == [8, 8, 8]


async def test_send_floodwait_exceeds_limit_returns_skipped(monkeypatch: pytest.MonkeyPatch):
    sleeps = _patch_sleep(monkeypatch)
    fw = _floodwait(1)
    msg = AsyncMock(side_effect=fw)
    client = _client(send_message=msg)

    r = await send(client, "chat-8", "hi", media_path=None)

    assert r.status == progress.STATUS_SKIPPED
    assert r.reason == "FloodWaitLimitExceeded"
    # 5 retries → 5 sleeps; no 6th.
    assert len(sleeps) == 5


async def test_send_floodwait_uses_caller_provided_seconds(monkeypatch: pytest.MonkeyPatch):
    sleeps = _patch_sleep(monkeypatch)
    fw = _floodwait(120)
    msg = AsyncMock(side_effect=[fw, None])
    client = _client(send_message=msg)

    await send(client, "chat-x", "hi", media_path=None)
    # 120 + 5 safety margin.
    assert sleeps == [125]


# ---------------------------------------------------------------------------
# send(): non-retryable outcomes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "make_exc",
    [
        lambda: ChatWriteForbiddenError(request=GetConfigRequest()),
        lambda: UserBannedInChannelError(request=GetConfigRequest()),
        lambda: ChannelPrivateError(request=GetConfigRequest()),
        lambda: SlowModeWaitError(request=GetConfigRequest(), capture="75"),
    ],
)
async def test_send_skipped_classifications(make_exc):
    msg = AsyncMock(side_effect=make_exc())
    client = _client(send_message=msg)

    r = await send(client, "chat-9", "hi", media_path=None)

    assert r.status == progress.STATUS_SKIPPED
    assert r.ok is False
    # Reason should contain the class name of the actual error that fired.
    assert "Error" in r.reason


async def test_send_generic_exception_returns_error_status():
    msg = AsyncMock(side_effect=ValueError("nope"))
    client = _client(send_message=msg)

    r = await send(client, "chat-10", "hi", media_path=None)

    assert r.status == progress.STATUS_ERROR
    assert r.ok is False
    assert "ValueError" in r.reason
    assert "nope" in r.reason


async def test_send_unknown_media_path_does_not_crash(monkeypatch: pytest.MonkeyPatch):
    """Extensionless / unknown media path becomes 'document' send."""
    monkeypatch.setattr(config, "FORCE_DOCUMENT", False)
    client = _client(send_file=AsyncMock())

    r = await send(client, "chat-11", "caption", media_path="weird.weird")

    assert r.ok is True
    assert r.reason == "with_media"
    assert client.send_file.await_args.kwargs["force_document"] is True


async def test_send_floodwait_exceeds_max_sleep_limit(monkeypatch: pytest.MonkeyPatch):
    sleeps = _patch_sleep(monkeypatch)
    monkeypatch.setattr(config, "MAX_FLOODWAIT_SLEEP_SEC", 100)
    
    # 200 seconds wait is requested. Safety margin adds +5 -> 205.
    # This exceeds 100 limit, so it should skip immediately without sleeping.
    fw = _floodwait(200)
    msg = AsyncMock(side_effect=fw)
    client = _client(send_message=msg)

    r = await send(client, "chat-x", "hi", media_path=None)

    assert r.status == progress.STATUS_SKIPPED
    assert r.reason == "FloodWaitExceeded:205"
    assert r.floodwait_seconds == 205
    assert sleeps == []  # Should not have slept at all!
