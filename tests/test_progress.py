"""Юнит-тесты progress.py: чистые преобразования + I/O (через tmp_path)."""

from __future__ import annotations

import json
from pathlib import Path

import progress

# --- Чистые преобразования ---


def test_should_skip_state_empty() -> None:
    assert progress.should_skip_state({}, 1) is False


def test_should_skip_state_sent_only() -> None:
    state = {"1": {"status": progress.STATUS_SENT, "reason": "", "ts": 0}}
    assert progress.should_skip_state(state, 1) is True


def test_should_skip_state_ignores_other_statuses() -> None:
    for status in (progress.STATUS_SKIPPED, progress.STATUS_ERROR):
        state = {"1": {"status": status, "reason": "x", "ts": 0}}
        assert progress.should_skip_state(state, 1) is False


def test_summarize_counts() -> None:
    state = {
        "1": {"status": progress.STATUS_SENT, "reason": "", "ts": 0},
        "2": {"status": progress.STATUS_SENT, "reason": "", "ts": 0},
        "3": {"status": progress.STATUS_SKIPPED, "reason": "SlowMode", "ts": 0},
        "4": {"status": progress.STATUS_ERROR, "reason": "boom", "ts": 0},
    }
    s = progress.summarize(state)
    assert s == {"sent": 2, "skipped": 1, "error": 1, "total": 4}


def test_report_from_has_counts() -> None:
    state = {"1": {"status": progress.STATUS_SENT, "ts": 0}}
    report = progress.report_from(state)
    assert "отправлено: 1" in report
    assert "Итого: 1" in report


# --- I/O слой (tmp_path) ---


def test_mark_and_load(tmp_path: Path) -> None:
    p = tmp_path / "progress.json"
    progress.mark_sent(100, p)
    progress.mark_skipped(101, "SlowModeWaitError", p)
    progress.mark_error(102, "ValueError('x')", p)

    state = progress.load(p)
    assert state["100"]["status"] == progress.STATUS_SENT
    assert state["101"]["status"] == progress.STATUS_SKIPPED
    assert state["102"]["reason"] == "ValueError('x')"


def test_should_skip_reads_file(tmp_path: Path) -> None:
    p = tmp_path / "progress.json"
    progress.mark_sent(7, p)
    assert progress.should_skip(7, p) is True
    assert progress.should_skip(8, p) is False


def test_resume_marks_only_sent_as_skip(tmp_path: Path) -> None:
    p = tmp_path / "progress.json"
    progress.mark_skipped(1, "no_rights", p)
    progress.mark_sent(2, p)
    assert progress.should_skip(1, p) is False  # пропущенный ранее — повторим
    assert progress.should_skip(2, p) is True


def test_reset_removes_file(tmp_path: Path) -> None:
    p = tmp_path / "progress.json"
    progress.mark_sent(1, p)
    assert p.exists()
    progress.reset(p)
    assert not p.exists()


def test_load_missing_file_is_empty(tmp_path: Path) -> None:
    p = tmp_path / "nope.json"
    assert progress.load(p) == {}


def test_load_corrupt_file_is_empty(tmp_path: Path) -> None:
    p = tmp_path / "progress.json"
    p.write_text("{ не валидный json", encoding="utf-8")
    assert progress.load(p) == {}


def test_json_keys_are_string_ids(tmp_path: Path) -> None:
    p = tmp_path / "progress.json"
    progress.mark_sent(12345, p)
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert "12345" in raw
