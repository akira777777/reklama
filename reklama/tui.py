from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime

try:
    import msvcrt
    _has_msvcrt = True
except ImportError:
    _has_msvcrt = False
    try:
        import select
        import termios
        import tty
        _has_termios = True
    except ImportError:
        _has_termios = False

from rich.align import Align
from rich.panel import Panel
from rich.progress import ProgressBar
from rich.table import Table
from rich.text import Text

EngineState = dict


class LiveLogHandler(logging.Handler):
    def __init__(self, max_records: int = 11):
        super().__init__()
        self.records: list[str] = []
        self.max_records = max_records

    def emit(self, record: logging.LogRecord) -> None:
        try:
            from reklama.utils import clean_control_chars
            clean_msg = clean_control_chars(record.getMessage())
            ts = datetime.now().strftime("%H:%M:%S")
            if record.levelno >= logging.ERROR:
                color = "red"
            elif record.levelno >= logging.WARNING:
                color = "yellow"
            elif "ОТПРАВЛЕНО" in clean_msg:
                color = "green"
            elif "ПРОПУСК" in clean_msg:
                color = "yellow"
            else:
                color = "white"
            self.records.append(f"[cyan]{ts}[/] [[{color}]{record.levelname}[/]] {clean_msg}")
            if len(self.records) > self.max_records:
                self.records.pop(0)
        except Exception:
            self.handleError(record)


async def keyboard_listener(state: EngineState) -> None:
    if not sys.stdin.isatty():
        return

    if _has_msvcrt:
        while state["running"]:
            if msvcrt.kbhit():  # type: ignore[attr-defined]
                try:
                    ch = msvcrt.getch()  # type: ignore[attr-defined]
                    if ch in (b'\xe0', b'\x00'):
                        msvcrt.getch()  # type: ignore[attr-defined]
                        continue
                    char = ch.decode("utf-8", errors="ignore").lower()
                    if char in ("p", " "):
                        state["paused"] = not state["paused"]
                    elif char == "s":
                        state["skip_delay"] = True
                    elif char == "q":
                        state["running"] = False
                except Exception:
                    pass
            await asyncio.sleep(0.05)
    elif _has_termios:
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)  # type: ignore[attr-defined]
        try:
            tty.setraw(fd)  # type: ignore[attr-defined]
            while state["running"]:
                rlist, _, _ = select.select([sys.stdin], [], [], 0.05)
                if rlist:
                    key = sys.stdin.read(1)
                    key_char = key.lower()
                    if key_char in ("p", " "):
                        state["paused"] = not state["paused"]
                    elif key_char == "s":
                        state["skip_delay"] = True
                    elif key_char == "q":
                        state["running"] = False
                await asyncio.sleep(0.05)
        except Exception:
            pass
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)  # type: ignore[attr-defined]


async def smart_sleep(duration: float, state: EngineState, state_label: str = "Ожидание") -> None:
    if duration <= 0:
        return
    state["state"] = state_label
    state["timer_total"] = duration
    state["timer_remaining"] = duration

    step = 0.1
    elapsed = 0.0
    while elapsed < duration and state["running"]:
        if state["skip_delay"]:
            state["skip_delay"] = False
            break
        while state["paused"] and state["running"]:
            state["state"] = "ПАУЗА"
            await asyncio.sleep(step)
        state["state"] = state_label
        await asyncio.sleep(step)
        elapsed += step
        state["timer_remaining"] = max(0.0, duration - elapsed)

    state["timer_total"] = 0.0
    state["timer_remaining"] = 0.0


def make_layout(log_handler: LiveLogHandler, state: EngineState) -> Table:
    grid = Table.grid(expand=True)
    grid.add_column()

    status_text = "[bold green]ЗАПУЩЕН[/]"
    if not state["running"]:
        status_text = "[bold red]ЗАВЕРШЕНИЕ[/]"
    elif state["paused"]:
        status_text = "[bold yellow]ПАУЗА[/]"
    elif "Ожидание" in state["state"] or "Перерыв" in state["state"]:
        status_text = "[bold blue]ОЖИДАНИЕ[/]"

    header_content = Text.assemble(
        ("Telegram Marketing campaign Orchestrator", "bold cyan"),
        ("  |  Статус: ", "white"),
        (status_text, ""),
    )
    grid.add_row(Panel(Align.center(header_content), border_style="cyan"))

    done = state["sent"] + state["skipped"] + state["errors"]
    total = max(1, state["total"])
    pct = (done / total) * 100

    prog_bar = ProgressBar(total=total, completed=done, width=None)
    prog_text = f"Прогресс: {pct:.1f}% ({done}/{total})"
    prog_table = Table.grid(expand=True)
    prog_table.add_column(ratio=7)
    prog_table.add_column(ratio=3, justify="right")
    prog_table.add_row(prog_bar, prog_text)
    grid.add_row(Panel(prog_table, title="Ход рассылки", border_style="blue"))

    mid_table = Table.grid(expand=True)
    mid_table.add_column(ratio=5)
    mid_table.add_column(ratio=5)

    stats_table = Table.grid(padding=(0, 1))
    stats_table.add_column("Параметр", style="cyan")
    stats_table.add_column("Значение", style="white")
    stats_table.add_row("Успешно отправлено:", f"[green]{state['sent']}[/]")
    stats_table.add_row("Пропущено (нет прав):", f"[yellow]{state['skipped']}[/]")
    stats_table.add_row("Ошибки отправки:", f"[red]{state['errors']}[/]")
    stats_table.add_row("Всего групп:", str(state["total"]))
    stats_panel = Panel(stats_table, title="Статистика", border_style="blue")

    action_table = Table.grid(padding=(0, 1))
    action_table.add_column("Свойство", style="cyan")
    action_table.add_column("Значение", style="white")

    grp_title = state["current_group"]
    if len(grp_title) > 28:
        grp_title = grp_title[:25] + "..."

    action_table.add_row("Текущая группа:", grp_title)
    action_table.add_row("Режим работы:", state["state"])

    timer_str = "-"
    if state["timer_remaining"] > 0:
        timer_str = f"{int(state['timer_remaining'])} сек (из {int(state['timer_total'])} сек)"
    action_table.add_row("Таймер задержки:", f"[bold magenta]{timer_str}[/]")
    action_table.add_row("Множитель задержек:", f"{state['delay_multiplier']:.2f}x")
    action_table.add_row("Окно активности:", state["active_hours"] or "Без ограничений")
    action_panel = Panel(action_table, title="Текущее действие", border_style="blue")

    mid_table.add_row(stats_panel, action_panel)
    grid.add_row(mid_table)

    log_lines = "\n".join(log_handler.records) if log_handler.records else "Нет событий."
    grid.add_row(Panel(log_lines, title="Журнал активности (последние события)", border_style="dim white"))

    footer_text = Text("[Space/P] Пауза/Старт  |  [S] Пропустить задержку  |  [Q] Безопасный выход", justify="center", style="bold yellow")
    grid.add_row(Panel(footer_text, border_style="dim yellow"))

    return grid
