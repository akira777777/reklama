from __future__ import annotations

import asyncio
import logging
from io import StringIO

import pytest
from rich.console import Console

from reklama.tui import LiveLogHandler, make_layout, smart_sleep


def _render_table(table) -> str:
    buf = StringIO()
    console = Console(
        file=buf, force_terminal=False, legacy_windows=False,
        width=120, no_color=True, _environ={},
    )
    console.print(table)
    return buf.getvalue()


class TestLiveLogHandler:
    def test_emit_stores_formatted_record(self):
        handler = LiveLogHandler(max_records=5)
        handler.setLevel(logging.DEBUG)
        logger = logging.getLogger("test_live_log_a")
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.info("test message")
        assert len(handler.records) == 1
        assert "test message" in handler.records[0]

    def test_emit_respects_max_records(self):
        handler = LiveLogHandler(max_records=3)
        handler.setLevel(logging.DEBUG)
        logger = logging.getLogger("test_live_log_max_a")
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()
        logger.addHandler(handler)
        for i in range(5):
            logger.info(f"msg {i}")
        assert len(handler.records) == 3
        assert "msg 2" in handler.records[0]
        assert "msg 4" in handler.records[2]

    def test_emit_colors_error_red(self):
        handler = LiveLogHandler(max_records=10)
        handler.setLevel(logging.DEBUG)
        logger = logging.getLogger("test_live_log_color_a")
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.error("fail")
        assert "red" in handler.records[0]

    def test_emit_colors_warning_yellow(self):
        handler = LiveLogHandler(max_records=10)
        handler.setLevel(logging.DEBUG)
        logger = logging.getLogger("test_live_log_warn_a")
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.warning("warn")
        assert "yellow" in handler.records[0]

    def test_emit_colors_success_green(self):
        handler = LiveLogHandler(max_records=10)
        handler.setLevel(logging.DEBUG)
        logger = logging.getLogger("test_live_log_ok_a")
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.info("ОТПРАВЛЕНО в группу")
        assert "green" in handler.records[0]

    def test_emit_colors_skip_yellow(self):
        handler = LiveLogHandler(max_records=10)
        handler.setLevel(logging.DEBUG)
        logger = logging.getLogger("test_live_log_skip_a")
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.info("ПРОПУСК группы")
        assert "yellow" in handler.records[0]

    def test_emit_includes_timestamp(self):
        from datetime import datetime
        handler = LiveLogHandler(max_records=10)
        handler.setLevel(logging.DEBUG)
        logger = logging.getLogger("test_live_log_ts_a")
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.info("ts check")
        now = datetime.now().strftime("%H:%M")
        assert now in handler.records[0]


class TestSmartSleep:
    @pytest.mark.asyncio
    async def test_zero_duration_returns_immediately(self):
        state = {"running": True, "paused": False, "skip_delay": False,
                 "state": "", "timer_total": 0.0, "timer_remaining": 0.0}
        await smart_sleep(0, state)
        assert state["timer_total"] == 0.0

    @pytest.mark.asyncio
    async def test_negative_duration_returns_immediately(self):
        state = {"running": True, "paused": False, "skip_delay": False,
                 "state": "", "timer_total": 0.0, "timer_remaining": 0.0}
        await smart_sleep(-1, state)
        assert state["timer_total"] == 0.0

    @pytest.mark.asyncio
    async def test_short_sleep_completes(self):
        state = {"running": True, "paused": False, "skip_delay": False,
                 "state": "", "timer_total": 0.0, "timer_remaining": 0.0}
        await smart_sleep(0.2, state)
        assert state["timer_total"] == 0.0
        assert state["timer_remaining"] == 0.0

    @pytest.mark.asyncio
    async def test_skip_delay_breaks_early(self):
        state = {"running": True, "paused": False, "skip_delay": False,
                 "state": "", "timer_total": 0.0, "timer_remaining": 0.0}

        async def _set_skip():
            await asyncio.sleep(0.05)
            state["skip_delay"] = True

        asyncio.create_task(_set_skip())
        await smart_sleep(5.0, state)
        assert state["timer_total"] == 0.0

    @pytest.mark.asyncio
    async def test_running_false_breaks_early(self):
        state = {"running": True, "paused": False, "skip_delay": False,
                 "state": "", "timer_total": 0.0, "timer_remaining": 0.0}

        async def _stop():
            await asyncio.sleep(0.05)
            state["running"] = False

        asyncio.create_task(_stop())
        await smart_sleep(5.0, state)
        assert state["timer_total"] == 0.0

    @pytest.mark.asyncio
    async def test_state_label_set_during_sleep(self):
        state = {"running": True, "paused": False, "skip_delay": False,
                 "state": "", "timer_total": 0.0, "timer_remaining": 0.0}

        async def _stop():
            await asyncio.sleep(0.05)
            state["running"] = False

        asyncio.create_task(_stop())
        await smart_sleep(5.0, state, state_label="TestLabel")
        assert state["state"] == "TestLabel"


class TestMakeLayout:
    def test_returns_table(self):
        from rich.table import Table
        handler = LiveLogHandler()
        state = {
            "running": True, "paused": False, "skip_delay": False,
            "finished": False, "state": "", "timer_total": 0.0,
            "timer_remaining": 0.0, "current_group": "",
            "delay_multiplier": 1.0, "sent": 0, "skipped": 0,
            "errors": 0, "total": 10, "active_hours": "",
        }
        result = make_layout(handler, state)
        assert isinstance(result, Table)

    def test_status_finished(self):
        handler = LiveLogHandler()
        state = {
            "running": False, "paused": False, "skip_delay": False,
            "finished": True, "state": "", "timer_total": 0.0,
            "timer_remaining": 0.0, "current_group": "test",
            "delay_multiplier": 1.0, "sent": 5, "skipped": 2,
            "errors": 1, "total": 10, "active_hours": "09:00-21:00",
        }
        table = make_layout(handler, state)
        rendered = _render_table(table)
        assert "ЗАВЕРШЕНИЕ" in rendered

    def test_status_paused(self):
        handler = LiveLogHandler()
        state = {
            "running": True, "paused": True, "skip_delay": False,
            "finished": False, "state": "", "timer_total": 0.0,
            "timer_remaining": 0.0, "current_group": "",
            "delay_multiplier": 1.0, "sent": 0, "skipped": 0,
            "errors": 0, "total": 10, "active_hours": "",
        }
        table = make_layout(handler, state)
        rendered = _render_table(table)
        assert "ПАУЗА" in rendered

    def test_group_name_truncated(self):
        handler = LiveLogHandler()
        state = {
            "running": True, "paused": False, "skip_delay": False,
            "finished": False, "state": "", "timer_total": 0.0,
            "timer_remaining": 0.0,
            "current_group": "A" * 50,
            "delay_multiplier": 1.0, "sent": 0, "skipped": 0,
            "errors": 0, "total": 10, "active_hours": "",
        }
        table = make_layout(handler, state)
        rendered = _render_table(table)
        assert "..." in rendered

    def test_empty_log_shows_placeholder(self):
        handler = LiveLogHandler()
        state = {
            "running": True, "paused": False, "skip_delay": False,
            "finished": False, "state": "", "timer_total": 0.0,
            "timer_remaining": 0.0, "current_group": "",
            "delay_multiplier": 1.0, "sent": 0, "skipped": 0,
            "errors": 0, "total": 10, "active_hours": "",
        }
        table = make_layout(handler, state)
        rendered = _render_table(table)
        assert "Нет событий" in rendered
