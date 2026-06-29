from __future__ import annotations

from reklama.engine import CampaignEngine


def _make_engine() -> CampaignEngine:
    return CampaignEngine()


def test_initial_state():
    e = _make_engine()
    s = e.state
    assert s["running"] is True
    assert s["paused"] is False
    assert s["skip_delay"] is False
    assert s["finished"] is False
    assert s["state"] == ""
    assert s["timer_total"] == 0.0
    assert s["timer_remaining"] == 0.0
    assert s["current_group"] == ""
    assert s["delay_multiplier"] == 1.0
    assert s["sent"] == 0
    assert s["skipped"] == 0
    assert s["errors"] == 0
    assert s["total"] == 0
    assert s["active_hours"] == ""


def test_reset_clears_and_sets_active_hours():
    e = _make_engine()
    e.state["sent"] = 42
    e.state["errors"] = 5
    e.state["finished"] = True
    e.state["running"] = False

    e.reset(active_hours="09:00-21:00")

    assert e.state["running"] is True
    assert e.state["paused"] is False
    assert e.state["skip_delay"] is False
    assert e.state["finished"] is False
    assert e.state["sent"] == 0
    assert e.state["errors"] == 0
    assert e.state["active_hours"] == "09:00-21:00"


def test_reset_default_active_hours():
    e = _make_engine()
    e.reset()
    assert e.state["active_hours"] == ""


def test_stop():
    e = _make_engine()
    e.stop()
    assert e.state["running"] is False
    assert e.state["finished"] is True


def test_pause():
    e = _make_engine()
    e.pause()
    assert e.state["paused"] is True
    assert e.state["running"] is True


def test_resume():
    e = _make_engine()
    e.pause()
    e.resume()
    assert e.state["paused"] is False


def test_resume_when_not_paused():
    e = _make_engine()
    e.resume()
    assert e.state["paused"] is False


def test_skip_delay():
    e = _make_engine()
    e.skip_delay()
    assert e.state["skip_delay"] is True


def test_state_is_same_dict_reference():
    e = _make_engine()
    assert e.state is e.state


def test_counters_are_mutable():
    e = _make_engine()
    e.state["sent"] = 10
    e.state["skipped"] = 3
    e.state["errors"] = 1
    assert e.state["sent"] == 10
    assert e.state["skipped"] == 3
    assert e.state["errors"] == 1


def test_stop_idempotent():
    e = _make_engine()
    e.stop()
    e.stop()
    assert e.state["running"] is False
    assert e.state["finished"] is True
