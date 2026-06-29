from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class CampaignEngine:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {
            "running": True,
            "paused": False,
            "skip_delay": False,
            "finished": False,
            "state": "",
            "timer_total": 0.0,
            "timer_remaining": 0.0,
            "current_group": "",
            "delay_multiplier": 1.0,
            "sent": 0,
            "skipped": 0,
            "errors": 0,
            "total": 0,
            "active_hours": "",
        }

    def reset(self, active_hours: str = "") -> None:
        self.state.update({
            "running": True,
            "paused": False,
            "skip_delay": False,
            "finished": False,
            "state": "",
            "timer_total": 0.0,
            "timer_remaining": 0.0,
            "current_group": "",
            "delay_multiplier": 1.0,
            "sent": 0,
            "skipped": 0,
            "errors": 0,
            "total": 0,
            "active_hours": active_hours,
        })

    def stop(self) -> None:
        self.state["running"] = False
        self.state["finished"] = True
        log.info("Кампания остановлена.")

    def pause(self) -> None:
        self.state["paused"] = True

    def resume(self) -> None:
        self.state["paused"] = False

    def skip_delay(self) -> None:
        self.state["skip_delay"] = True
