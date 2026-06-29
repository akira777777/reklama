from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from reklama.config import ActiveWindow
from run import _clean, seconds_until_window, within_active_window


def test_clean_control_chars():
    assert _clean("Group\x00Name\x1fTitle\x7f") == "Group Name Title "


@patch("run.datetime")
def test_within_active_window(mock_datetime):
    # Current time: 10:30 (630 mins)
    mock_datetime.now.return_value = datetime(2026, 6, 29, 10, 30)

    # Active window: 09:00 - 18:00 (540 to 1080 mins)
    w = ActiveWindow(540, 1080)
    assert within_active_window(w) is True

    # Active window: 12:00 - 18:00 (720 to 1080 mins)
    w2 = ActiveWindow(720, 1080)
    assert within_active_window(w2) is False


@patch("run.datetime")
def test_seconds_until_window(mock_datetime):
    # Current time: 10:30 (630 mins)
    mock_datetime.now.return_value = datetime(2026, 6, 29, 10, 30)

    # Target window starts at 12:00 (720 mins)
    # Difference: 90 mins = 5400 seconds
    w = ActiveWindow(720, 1080)
    assert seconds_until_window(w) == 5400
