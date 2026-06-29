from __future__ import annotations

import pytest

import config
import progress
from sender import SendResult, detect_media_kind


def test_send_result_ok():
    r1 = SendResult(progress.STATUS_SENT)
    assert r1.ok is True

    r2 = SendResult(progress.STATUS_SKIPPED)
    assert r2.ok is False

    r3 = SendResult(progress.STATUS_ERROR)
    assert r3.ok is False


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
