from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from progress import (
    STATUS_ERROR,
    STATUS_SENT,
    STATUS_SKIPPED,
    apply,
    load,
    report_from,
    reset,
    save,
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
