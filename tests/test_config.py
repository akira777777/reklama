from __future__ import annotations

from pathlib import Path

import pytest

from config import (
    ActiveWindow,
    has_credentials,
    parse_active_hours,
    resolve_media_path,
)


def test_parse_active_hours_valid():
    result = parse_active_hours("09:00-21:00")
    assert result is not None
    assert result == ActiveWindow(start_min=540, end_min=1260)


def test_parse_active_hours_midnight_cross():
    result = parse_active_hours("22:00-06:00")
    assert result is not None
    assert result == ActiveWindow(start_min=1320, end_min=360)


def test_parse_active_hours_empty_string():
    assert parse_active_hours("") is None


def test_parse_active_hours_no_dash():
    assert parse_active_hours("0900-2100") is None  # Requires HH:MM format


def test_parse_active_hours_short_hour():
    # "9:00-21:00" is actually valid per the parser (splits on : then -)
    assert parse_active_hours("9:00-21:00") == ActiveWindow(start_min=540, end_min=1260)


def test_parse_active_hours_out_of_range():
    assert parse_active_hours("25:00-26:00") is None


def test_parse_active_hours_single_time():
    assert parse_active_hours("09:00") is None


def test_parse_active_hours_none():
    assert parse_active_hours(None) is None  # type: ignore[arg-type]


def test_active_window_repr():
    window = ActiveWindow(start_min=60, end_min=120)
    assert "start_min" in repr(window)


def test_resolve_media_path_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dummy_file = tmp_path / "my_media.jpg"
    dummy_file.write_text("dummy", encoding="utf-8")
    monkeypatch.setenv("MEDIA_PATH", str(dummy_file))
    result = resolve_media_path()
    assert result == str(dummy_file)


def test_resolve_media_path_from_env_not_a_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_path = str(tmp_path / "nonexistent" / "file.txt")
    monkeypatch.setenv("MEDIA_PATH", env_path)
    result = resolve_media_path()
    # When MEDIA_PATH is invalid, function falls through to media/ dir
    # which may contain .gitkeep, so just verify it did not return env_path
    assert result != env_path


def test_resolve_media_path_from_media_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Point BASE_DIR to tmp_path for this test
    import config as config_module

    monkeypatch.setattr(config_module, "BASE_DIR", tmp_path)
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (media_dir / "first.jpg").write_text("dummy", encoding="utf-8")
    (media_dir / "second.jpg").write_text("dummy2", encoding="utf-8")
    result = resolve_media_path()
    assert result is not None
    assert Path(result).name == "first.jpg"


def test_resolve_media_path_no_media_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import config as config_module

    monkeypatch.setattr(config_module, "BASE_DIR", tmp_path)
    result = resolve_media_path()
    assert result is None


def test_resolve_media_path_empty_media_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import config as config_module

    monkeypatch.setattr(config_module, "BASE_DIR", tmp_path)
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    result = resolve_media_path()
    assert result is None


def test_has_credentials_true():

    assert has_credentials() is True
