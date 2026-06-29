from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from progress import (
    STATUS_ERROR,
    STATUS_SENT,
    STATUS_SKIPPED,
    apply,
    load,
    mark,
    mark_error,
    mark_sent,
    mark_skipped,
    report,
    report_from,
    reset,
    save,
    should_skip,
    should_skip_state,
    summarize,
)


def test_should_skip_state_found():
    state: dict[str, Any] = {"123": {"status": STATUS_SENT, "reason": "", "ts": 0.0}}
    assert should_skip_state(state, 123) is True


def test_should_skip_state_not_found():
    state: dict[str, Any] = {}
    assert should_skip_state(state, 123) is False


def test_should_skip_state_different_status():
    state = {"123": {"status": STATUS_ERROR, "reason": "boom", "ts": 0.0}}
    assert should_skip_state(state, 123) is False


def test_should_skip_state_non_dict_entry():
    state = {"123": "not_a_dict"}
    assert should_skip_state(state, 123) is False


def test_summarize():
    state = {
        "1": {"status": STATUS_SENT},
        "2": {"status": STATUS_SKIPPED, "reason": "no_access"},
        "3": {"status": STATUS_ERROR, "reason": "timeout"},
        "4": {"status": STATUS_SENT},
        "5": {"status": "unknown"},
    }
    result = summarize(state)
    assert result[STATUS_SENT] == 2
    assert result[STATUS_SKIPPED] == 1
    assert result[STATUS_ERROR] == 1
    assert result["total"] == 5


def test_report_from():
    state = {
        "1": {"status": STATUS_SENT},
        "2": {"status": STATUS_SKIPPED},
        "3": {"status": STATUS_ERROR},
    }
    report = report_from(state)
    assert "Итого: 3" in report
    assert "отправлено: 1" in report
    assert "пропущено: 1" in report
    assert "ошибок: 1" in report


def test_apply_and_save(tmp_path: Path) -> None:
    state: dict[str, Any] = {}
    apply(state, 100, STATUS_SENT)
    assert state[str(100)]["status"] == STATUS_SENT

    save(state, tmp_path / "progress_test.json")
    loaded = load(tmp_path / "progress_test.json")
    assert str(100) in loaded
    assert loaded[str(100)]["status"] == STATUS_SENT


def test_load_nonexistent_file(tmp_path: Path) -> None:
    result = load(tmp_path / "nonexistent.json")
    assert result == {}


def test_load_corrupted_file(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("not valid json{", encoding="utf-8")
    result = load(bad_file)
    assert result == {}


def test_reset(tmp_path: Path) -> None:
    p = tmp_path / "progress_to_reset.json"
    save({"1": {"status": STATUS_SENT}}, p)
    assert p.exists()
    reset(p)
    assert not p.exists()


def test_reset_nonexistent_file(tmp_path: Path) -> None:
    p = tmp_path / "no_file.json"
    reset(p)  # Should not raise


def test_apply_sets_timestamp():
    state: dict[str, Any] = {}
    before = datetime.now(tz=UTC).timestamp()
    apply(state, 42, STATUS_SENT, "all good")
    after = datetime.now(tz=UTC).timestamp()
    entry = state[str(42)]
    assert entry["status"] == STATUS_SENT
    assert entry["reason"] == "all good"
    assert before <= entry["ts"] <= after


# ---------------------------------------------------------------------------
# Hardening: mark_*, should_skip (file), report (file), non-dict JSON
# ---------------------------------------------------------------------------


def test_mark_persists_and_returns_state(tmp_path: Path):
    p = tmp_path / "p.json"
    state = mark(7, STATUS_SENT, "", p)

    assert str(7) in state
    assert state[str(7)]["status"] == STATUS_SENT
    assert p.exists()
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert on_disk[str(7)]["status"] == STATUS_SENT


def test_mark_preserves_other_entries(tmp_path: Path):
    p = tmp_path / "p.json"
    p.write_text(
        json.dumps({"1": {"status": STATUS_SENT, "reason": "", "ts": 0.0}}),
        encoding="utf-8",
    )
    state = mark(2, STATUS_ERROR, "boom", p)
    assert str(1) in state and str(2) in state


def test_mark_overwrites_existing_entry(tmp_path: Path):
    p = tmp_path / "p.json"
    mark(5, STATUS_ERROR, "first", p)
    state = mark(5, STATUS_SENT, "retried_ok", p)
    assert state[str(5)]["status"] == STATUS_SENT
    assert state[str(5)]["reason"] == "retried_ok"


@pytest.mark.parametrize(
    "fn, expected_status, expected_reason",
    [
        (mark_sent, STATUS_SENT, ""),
        (lambda cid, p: mark_skipped(cid, "no_write", p), STATUS_SKIPPED, "no_write"),
        (lambda cid, p: mark_error(cid, "boom", p), STATUS_ERROR, "boom"),
    ],
)
def test_convenience_helpers(tmp_path: Path, fn, expected_status: str, expected_reason: str):
    p = tmp_path / "p.json"
    fn(11, p)
    state = load(p)
    assert state[str(11)]["status"] == expected_status
    assert state[str(11)]["reason"] == expected_reason


def test_should_skip_file_returns_true_for_sent(tmp_path: Path):
    p = tmp_path / "p.json"
    mark_sent(33, p)
    assert should_skip(33, p) is True


def test_should_skip_file_returns_false_for_error(tmp_path: Path):
    p = tmp_path / "p.json"
    mark_error(33, "boom", p)
    assert should_skip(33, p) is False


def test_should_skip_file_returns_false_when_missing(tmp_path: Path):
    assert should_skip(33, tmp_path / "missing.json") is False


def test_report_file_renders_summary(tmp_path: Path):
    p = tmp_path / "p.json"
    mark_sent(1, p)
    mark_skipped(2, "no_write", p)
    mark_error(3, "boom", p)

    text = report(p)
    assert "Итого: 3" in text
    assert "отправлено: 1" in text
    assert "пропущено: 1" in text
    assert "ошибок: 1" in text


def test_load_non_dict_json_returns_empty(tmp_path: Path):
    """A file containing a JSON list or other non-dict must be treated as empty,
    not crash with TypeError on later dict-only access."""
    p = tmp_path / "list.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    assert load(p) == {}


def test_save_creates_parent_directory(tmp_path: Path):
    nested = tmp_path / "deeply" / "nested" / "p.json"
    save({"1": {"status": STATUS_SENT}}, nested)
    assert nested.exists()


def test_load_empty_file_returns_empty(tmp_path: Path):
    """An empty file is invalid JSON — must be treated as empty state, not crash."""
    p = tmp_path / "empty.json"
    p.write_text("", encoding="utf-8")
    assert load(p) == {}
