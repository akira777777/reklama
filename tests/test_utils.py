from __future__ import annotations

import logging
from pathlib import Path

import pytest

from reklama import utils
from reklama.utils import (
    clean_control_chars,
    setup_logging,
)

# ---------------------------------------------------------------------------
# clean_control_chars()
# ---------------------------------------------------------------------------


def test_clean_control_chars_removes_c0_controls():
    """C0 ASCII control chars (0x00..0x1F) must be replaced by spaces."""
    out = clean_control_chars("abc\x01\x02\x03def")
    assert out == "abc   def"
    assert "\x01" not in out and "\x02" not in out and "\x03" not in out


def test_clean_control_chars_removes_del():
    """0x7F (DEL) must be sanitised."""
    out = clean_control_chars("hello\x7fworld")
    assert out == "hello world"


def test_clean_control_chars_replaces_newlines_and_tabs():
    """Newlines and tabs are also control chars; they should not leak into logs."""
    out = clean_control_chars("line1\nline2\tend")
    assert "\n" not in out and "\t" not in out


def test_clean_control_chars_keeps_unicode_letters():
    assert clean_control_chars("Привет мир 🌍") == "Привет мир 🌍"


def test_clean_control_chars_empty_string():
    assert clean_control_chars("") == ""


def test_clean_control_chars_coerces_non_string():
    """Non-string inputs must not crash; they are coerced via str()."""
    assert clean_control_chars(123) == "123"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# setup_logging()
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _drop_non_pytest_root_handlers():
    """Pytest's logging plugin re-adds its own handler via autouse fixtures
    that run AFTER ours. We can only control handlers *we* add. Therefore
    setup_logging's 'first time' branch is exercised via dedicated tests
    below that isolate from pytest's handler."""
    # No-op fixture; documented for clarity.
    yield


def test_setup_logging_under_pytest_still_returns_valid_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The return value contract must always hold — even when pytest's logging
    plugin already populated the root handlers."""
    monkeypatch.setattr(utils.config, "BASE_DIR", tmp_path)

    log_file = setup_logging("session")

    assert log_file.parent == tmp_path / "logs"
    assert log_file.suffix == ".log"
    assert log_file.name.startswith("session_")


def test_setup_logging_returns_path_in_correct_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(utils.config, "BASE_DIR", tmp_path)
    log_file = setup_logging("anyname")
    assert log_file.parent.parent == tmp_path


def test_setup_logging_filename_format(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """<log_name>_<YYYYMMDD>_<HHMMSS>.log"""
    import re

    monkeypatch.setattr(utils.config, "BASE_DIR", tmp_path)
    log_file = setup_logging("fmt")
    assert re.match(r"^fmt_\d{8}_\d{6}\.log$", log_file.name), log_file.name


def test_setup_logging_attaches_handlers_when_root_empty(tmp_path: Path):
    """When the root logger has no handlers, setup_logging wires up both
    a StreamHandler and a FileHandler.

    Done by hand here (no pytest plugin interference) to genuinely test the
    'first invocation' branch of the implementation.
    """
    # Pre-clean root handlers so we exercise the cold-start branch.
    saved = list(logging.getLogger().handlers)
    logging.getLogger().handlers.clear()
    try:
        # Patch BASE_DIR for this test only.
        import reklama.config as config_module

        original_base = config_module.BASE_DIR
        config_module.BASE_DIR = tmp_path
        try:
            log_file = setup_logging("cold")
            root = logging.getLogger()
            assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)
            assert any(isinstance(h, logging.FileHandler) for h in root.handlers)
            # File IS created on FileHandler construction.
            assert log_file.exists()
        finally:
            config_module.BASE_DIR = original_base
    finally:
        logging.getLogger().handlers.clear()
        for h in saved:
            logging.getLogger().addHandler(h)


def test_setup_logging_is_idempotent(tmp_path: Path):
    """Two consecutive calls must not duplicate handlers on the root logger."""
    saved = list(logging.getLogger().handlers)
    logging.getLogger().handlers.clear()
    try:
        import reklama.config as config_module

        original_base = config_module.BASE_DIR
        config_module.BASE_DIR = tmp_path
        try:
            setup_logging("once")
            count_after_first = len(logging.getLogger().handlers)
            setup_logging("twice")
            count_after_second = len(logging.getLogger().handlers)
            assert count_after_first == count_after_second
        finally:
            config_module.BASE_DIR = original_base
    finally:
        logging.getLogger().handlers.clear()
        for h in saved:
            logging.getLogger().addHandler(h)


def test_setup_logging_writes_log_to_file(tmp_path: Path):
    """Emitted log records must reach the on-disk file."""
    saved = list(logging.getLogger().handlers)
    logging.getLogger().handlers.clear()
    try:
        import reklama.config as config_module

        original_base = config_module.BASE_DIR
        config_module.BASE_DIR = tmp_path
        try:
            log_file = setup_logging("writes")
            logging.getLogger("test_logger_x").info("hello-from-test")
            for h in logging.getLogger().handlers:
                h.flush()
            contents = log_file.read_text(encoding="utf-8")
            assert "hello-from-test" in contents
        finally:
            config_module.BASE_DIR = original_base
    finally:
        for h in logging.getLogger().handlers:
            h.flush()
        # Don't restore caplog handlers — they'll be restored by pytest.
        logging.getLogger().handlers.clear()
        for h in saved:
            logging.getLogger().addHandler(h)


def test_mutate_message_preserves_emoji_tags(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(utils.config, "MUTATE_MESSAGE", True)
    text = "Hello [emoji:12345] world! [emoji:6789]"
    mutated = utils.mutate_message(text)

    # Emoji tags must remain completely intact so parse_custom_emoji can match them
    assert "[emoji:12345]" in mutated
    assert "[emoji:6789]" in mutated

    # Zero-width space, non-joiner, joiner, or BOM must be injected
    invisible_chars = {"\u200b", "\u200c", "\u200d", "\ufeff"}
    assert any(c in mutated for c in invisible_chars)


def test_mutate_message_disabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(utils.config, "MUTATE_MESSAGE", False)
    text = "Hello world!"
    assert utils.mutate_message(text) == text
