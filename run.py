"""CLI-оркестратор рассылки: argparse + основной цикл с задержками и TUI-интерфейсом."""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import re
import sys
from datetime import datetime
from typing import Any

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

import contextlib

from rich.align import Align
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import ProgressBar
from rich.table import Table
from rich.text import Text

from telethon import TelegramClient

from reklama import auth, config, dialogs, emoji, progress, sender
from reklama.utils import mutate_message, setup_logging

log = logging.getLogger("run")


# Управляющие символы в названиях чатов (задаются админами групп) — нейтрализуем
# для защиты логов/терминала от инъекций (CWE-117).
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


# Глобальное состояние для управления TUI и логикой прерывания/паузы
control_state: dict[str, Any] = {
    "running": True,
    "paused": False,
    "skip_delay": False,
    "state": "Инициализация",
    "timer_total": 0.0,
    "timer_remaining": 0.0,
    "current_group": "Нет",
    "delay_multiplier": 1.0,
    "sent": 0,
    "skipped": 0,
    "errors": 0,
    "total": 0,
    "active_hours": "",
}


class LiveLogHandler(logging.Handler):
    """Логический хендлер, который собирает последние логи для отображения в TUI."""
    def __init__(self, max_records=11):
        super().__init__()
        self.records = []
        self.max_records = max_records

    def emit(self, record):
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


async def keyboard_listener() -> None:
    """Асинхронный слушатель клавиатуры для управления паузой, пропуском и выходом."""
    if not sys.stdin.isatty():
        return

    if _has_msvcrt:
        while control_state["running"]:
            if msvcrt.kbhit():  # type: ignore[attr-defined]
                try:
                    ch = msvcrt.getch()  # type: ignore[attr-defined]
                    if ch in (b'\xe0', b'\x00'):
                        msvcrt.getch()  # type: ignore[attr-defined]  # сбросить расширенный код
                        continue
                    char = ch.decode("utf-8", errors="ignore").lower()
                    if char in ("p", " "):
                        control_state["paused"] = not control_state["paused"]
                    elif char == "s":
                        control_state["skip_delay"] = True
                    elif char == "q":
                        control_state["running"] = False
                except Exception:
                    pass
            await asyncio.sleep(0.05)
    elif _has_termios:
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            while control_state["running"]:
                rlist, _, _ = select.select([sys.stdin], [], [], 0.05)
                if rlist:
                    ch = sys.stdin.read(1)
                    char = ch.lower()
                    if char in ("p", " "):
                        control_state["paused"] = not control_state["paused"]
                    elif char == "s":
                        control_state["skip_delay"] = True
                    elif char == "q":
                        control_state["running"] = False
                await asyncio.sleep(0.05)
        except Exception:
            pass
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


async def smart_sleep(duration: float, state_label: str = "Ожидание") -> None:
    """Умное ожидание, которое обновляет таймер и прерывается по запросу."""
    if duration <= 0:
        return
    control_state["state"] = state_label
    control_state["timer_total"] = duration
    control_state["timer_remaining"] = duration
    
    step = 0.1
    elapsed = 0.0
    while elapsed < duration and control_state["running"]:
        if control_state["skip_delay"]:
            control_state["skip_delay"] = False
            break
            
        while control_state["paused"] and control_state["running"]:
            control_state["state"] = "ПАУЗА"
            await asyncio.sleep(step)
            
        control_state["state"] = state_label
        await asyncio.sleep(step)
        elapsed += step
        control_state["timer_remaining"] = max(0.0, duration - elapsed)
        
    control_state["timer_total"] = 0.0
    control_state["timer_remaining"] = 0.0


def make_layout(log_handler: LiveLogHandler) -> Table:
    """Собирает и возвращает структуру интерфейса (Layout) в виде таблицы rich."""
    grid = Table.grid(expand=True)
    grid.add_column()
    
    # 1. Заголовок
    status_text = "[bold green]ЗАПУЩЕН[/]"
    if not control_state["running"]:
        status_text = "[bold red]ЗАВЕРШЕНИЕ[/]"
    elif control_state["paused"]:
        status_text = "[bold yellow]ПАУЗА[/]"
    elif "Ожидание" in control_state["state"] or "Перерыв" in control_state["state"]:
        status_text = "[bold blue]ОЖИДАНИЕ[/]"
        
    header_content = Text.assemble(
        ("Telegram Marketing campaign Orchestrator", "bold cyan"),
        ("  |  Статус: ", "white"),
        (status_text, "")
    )
    grid.add_row(Panel(Align.center(header_content), border_style="cyan"))
    
    # 2. Прогресс-бар
    done = control_state["sent"] + control_state["skipped"] + control_state["errors"]
    total = max(1, control_state["total"])
    pct = (done / total) * 100
    
    prog_bar = ProgressBar(total=total, completed=done, width=None)
    prog_text = f"Прогресс: {pct:.1f}% ({done}/{total})"
    prog_table = Table.grid(expand=True)
    prog_table.add_column(ratio=7)
    prog_table.add_column(ratio=3, justify="right")
    prog_table.add_row(prog_bar, prog_text)
    grid.add_row(Panel(prog_table, title="Ход рассылки", border_style="blue"))
    
    # 3. Средний блок (Инфо + Статистика)
    mid_table = Table.grid(expand=True)
    mid_table.add_column(ratio=5)
    mid_table.add_column(ratio=5)
    
    # Панель статистики
    stats_table = Table.grid(padding=(0, 1))
    stats_table.add_column("Параметр", style="cyan")
    stats_table.add_column("Значение", style="white")
    stats_table.add_row("Успешно отправлено:", f"[green]{control_state['sent']}[/]")
    stats_table.add_row("Пропущено (нет прав):", f"[yellow]{control_state['skipped']}[/]")
    stats_table.add_row("Ошибки отправки:", f"[red]{control_state['errors']}[/]")
    stats_table.add_row("Всего групп:", str(control_state["total"]))
    stats_panel = Panel(stats_table, title="Статистика", border_style="blue")
    
    # Панель текущего действия
    action_table = Table.grid(padding=(0, 1))
    action_table.add_column("Свойство", style="cyan")
    action_table.add_column("Значение", style="white")
    
    grp_title = control_state["current_group"]
    if len(grp_title) > 28:
        grp_title = grp_title[:25] + "..."
        
    action_table.add_row("Текущая группа:", grp_title)
    action_table.add_row("Режим работы:", control_state["state"])
    
    timer_str = "-"
    if control_state["timer_remaining"] > 0:
        timer_str = f"{int(control_state['timer_remaining'])} сек (из {int(control_state['timer_total'])} сек)"
    action_table.add_row("Таймер задержки:", f"[bold magenta]{timer_str}[/]")
    action_table.add_row("Множитель задержек:", f"{control_state['delay_multiplier']:.2f}x")
    action_table.add_row("Окно активности:", control_state["active_hours"] or "Без ограничений")
    action_panel = Panel(action_table, title="Текущее действие", border_style="blue")
    
    mid_table.add_row(stats_panel, action_panel)
    grid.add_row(mid_table)
    
    # 4. Лог-панель
    log_lines = "\n".join(log_handler.records) if log_handler.records else "Нет событий."
    grid.add_row(Panel(log_lines, title="Журнал активности (последние события)", border_style="dim white"))
    
    # 5. Подсказки
    footer_text = Text("[Space/P] Пауза/Старт  |  [S] Пропустить задержку  |  [Q] Безопасный выход", justify="center", style="bold yellow")
    grid.add_row(Panel(footer_text, border_style="dim yellow"))
    
    return grid


def _clean(title: str) -> str:
    return _CONTROL_RE.sub(" ", str(title))


class SpintaxNode:
    def resolve(self) -> str:
        raise NotImplementedError()

class SpintaxText(SpintaxNode):
    def __init__(self, text: str):
        self.text = text
    def resolve(self) -> str:
        return self.text

class SpintaxChoice(SpintaxNode):
    def __init__(self):
        self.options: list[list[SpintaxNode]] = [[]]
    def add_to_current(self, node: SpintaxNode):
        self.options[-1].append(node)
    def new_option(self):
        self.options.append([])
    def resolve(self) -> str:
        if not self.options or not self.options[0]:
            return ""
        chosen = random.choice(self.options)
        return "".join(node.resolve() for node in chosen)

def resolve_spintax(text: str) -> str:
    """Разрешает spintax вида {вариант1|вариант2|вариант3}.

    Поддерживает вложенность, например {Привет|Здравствуйте {друг|коллега}}.
    Поддерживает экранирование символов: \\{, \\}, \\| и самих бэкслешей \\\\.
    """
    if not text:
        return ""

    root: list[SpintaxNode] = []
    stack: list[SpintaxChoice] = []
    current_list: list[SpintaxNode] = root

    i = 0
    n = len(text)
    current_text: list[str] = []

    def flush_text():
        if current_text:
            current_list.append(SpintaxText("".join(current_text)))
            current_text.clear()

    while i < n:
        char = text[i]
        if char == "\\":
            if i + 1 < n:
                next_char = text[i+1]
                if next_char in ("{", "}", "|", "\\"):
                    current_text.append(next_char)
                    i += 2
                    continue
            current_text.append(char)
            i += 1
        elif char == "{":
            flush_text()
            node = SpintaxChoice()
            stack.append(node)
            current_list = node.options[0]
            i += 1
        elif char == "}":
            flush_text()
            if stack:
                node = stack.pop()
                current_list = stack[-1].options[-1] if stack else root
                current_list.append(node)
            else:
                current_text.append(char)
            i += 1
        elif char == "|":
            flush_text()
            if stack:
                stack[-1].new_option()
                current_list = stack[-1].options[-1]
            else:
                current_text.append(char)
            i += 1
        else:
            current_text.append(char)
            i += 1

    flush_text()

    while stack:
        node = stack.pop()
        if stack:
            stack[-1].options[-1].append(node)
        else:
            root.append(node)

    return "".join(node.resolve() for node in root)


async def _record(state: dict, eid: int, status: str, reason: str) -> None:
    """Обновляет состояние в памяти и асинхронно (без блокировки цикла) пишет файл."""
    progress.apply(state, eid, status, reason)
    await asyncio.to_thread(progress.save, state)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Рассылка рекламы по группам Telegram (Telethon).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Показать список групп и пропуски без отправки.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Ограничить число групп (для дымового теста).",
    )
    p.add_argument(
        "--reset-progress",
        action="store_true",
        help="Удалить progress.json перед запуском.",
    )
    p.add_argument(
        "--no-tui",
        action="store_true",
        help="Отключить графический интерфейс (TUI) в терминале.",
    )
    return p.parse_args()


def read_message() -> str:
    path = config.BASE_DIR / config.MESSAGE_FILE
    if not path.exists():
        log.error("Файл сообщения не найден: %s", path)
        sys.exit(1)
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        log.error("Файл сообщения пустой: %s", path)
        sys.exit(1)
    return text


def within_active_window(window: config.ActiveWindow) -> bool:
    now = datetime.now()
    cur = now.hour * 60 + now.minute
    if window.start_min <= window.end_min:
        return window.start_min <= cur < window.end_min
    return cur >= window.start_min or cur < window.end_min


def seconds_until_window(window: config.ActiveWindow) -> int:
    now = datetime.now()
    cur = now.hour * 60 + now.minute
    target = window.start_min  # даже при переходе через полночь ждём начала окна
    delta = target - cur
    if delta <= 0:
        delta += 24 * 60
    return delta * 60


async def ensure_active(window: config.ActiveWindow | None) -> None:
    """Если задано окно активности и сейчас вне его — ждём до открытия."""
    if window is None:
        return
    while not within_active_window(window) and control_state["running"]:
        wait = max(60, seconds_until_window(window))
        log.warning("Вне окна активности (%s) — ждём %d сек.", config.ACTIVE_HOURS, wait)
        await smart_sleep(min(wait, 3600), "Ожидание окна активности")


async def run(
    client: TelegramClient | None = None,
    dry_run: bool | None = None,
    limit: int | None = None,
    reset_progress: bool | None = None,
    no_tui: bool = True,
) -> None:
    is_programmatic = client is not None or dry_run is not None or limit is not None or reset_progress is not None

    if not is_programmatic:
        args = parse_args()
        p_dry_run = args.dry_run
        p_limit = args.limit
        p_reset_progress = args.reset_progress
        p_no_tui = args.no_tui
    else:
        p_dry_run = bool(dry_run)
        p_limit = limit
        p_reset_progress = bool(reset_progress)
        p_no_tui = bool(no_tui)

    log_file = setup_logging("run")
    log.info("Лог запуска: %s", log_file)

    if p_reset_progress:
        progress.reset()

    text_template = read_message()
    media = config.resolve_media_path()
    
    # Инициализация глобального состояния
    control_state["active_hours"] = config.ACTIVE_HOURS
    control_state["running"] = True
    control_state["paused"] = False
    control_state["skip_delay"] = False
    
    window = config.parse_active_hours(config.ACTIVE_HOURS)
    
    # Проверка интерактивности и флага --no-tui
    use_tui = not p_no_tui and sys.stdout.isatty()
    
    tui_log_handler = None
    live = None
    listener_task = None
    updater_task = None
    
    root = logging.getLogger()
    
    if use_tui:
        # Настройка кастомного хендлера логов для TUI
        tui_log_handler = LiveLogHandler()
        root.addHandler(tui_log_handler)
        
        # Понижаем уровень вывода стандартного StreamHandler, чтобы он не писал в stdout напрямую
        for h in root.handlers:
            if isinstance(h, logging.StreamHandler) and h != tui_log_handler:
                h.setLevel(logging.CRITICAL + 1)
                
        # Запуск фонового прослушивания клавиатуры
        listener_task = asyncio.create_task(keyboard_listener())
        
        # Создание и запуск Live-экрана rich
        console = Console()
        live = Live(make_layout(tui_log_handler), console=console, screen=False, auto_refresh=True, refresh_per_second=4)
        live.start()
        
        # Фоновый апдейтер экрана
        async def dashboard_updater():
            while control_state["running"]:
                with contextlib.suppress(Exception):
                    live.update(make_layout(tui_log_handler))
                await asyncio.sleep(0.25)
        updater_task = asyncio.create_task(dashboard_updater())
        
        control_state["state"] = "Инициализация клиента..."
    
    try:
        if media:
            log.info("Медиа: %s (вид: %s)", media, sender.detect_media_kind(media))
        else:
            log.info("Медиа не найдено — рассылка только текстом.")

        if window:
            log.info("Окно активности: %s", config.ACTIVE_HOURS)

        async def execute_campaign(client_obj):
            if not p_dry_run and control_state["running"]:
                if use_tui:
                    control_state["state"] = "Самопроверка..."
                if not await auth.check_self(client_obj):
                    log.critical("Самопроверка аккаунта не удалась. Завершаем работу.")
                    if is_programmatic:
                        raise RuntimeError("Самопроверка аккаунта не удалась")
                    else:
                        sys.exit(1)

            if not control_state["running"]:
                return

            if use_tui:
                control_state["state"] = "Сбор групп..."
            groups = await dialogs.collect_groups(client_obj)
            if p_limit is not None:
                groups = groups[: p_limit]
            total = len(groups)
            control_state["total"] = total
            
            log.info("Кандидатов к рассылке: %d.", total)
            if total == 0:
                log.warning("Группы не найдены — нечего рассылать.")
                return

            state = progress.load()
            
            # Синхронизируем статистику с сохраненным прогрессом
            stats = progress.summarize(state)
            control_state["sent"] = stats[progress.STATUS_SENT]
            control_state["skipped"] = stats[progress.STATUS_SKIPPED]
            control_state["errors"] = stats[progress.STATUS_ERROR]

            if p_dry_run:
                if use_tui:
                    control_state["state"] = "DRY RUN проход..."
                log.info("=== DRY RUN ===")
                sample_resolved = resolve_spintax(text_template)
                sample_mutated = mutate_message(sample_resolved)
                sample_clean, _ = emoji.parse_custom_emoji(sample_mutated)
                log.info("Пример сообщения после spintax и мутации:\n---\n%s\n---", sample_clean)
                skipped_count = sum(
                    1 for eid, _title, _entity in groups if progress.should_skip_state(state, eid)
                )
                for eid, title, _entity in groups:
                    if not control_state["running"]:
                        break
                    flag = " [уже отправлено]" if progress.should_skip_state(state, eid) else ""
                    log.info("  - %s (id=%d)%s", _clean(title), eid, flag)
                log.info("Будет пропущено (resume): %d из %d.", skipped_count, total)
                if use_tui and control_state["running"]:
                    # Даем пользователю посмотреть на TUI при dry-run, если не было прервано
                    await smart_sleep(5.0, "DRY RUN Завершен")
                return

            done = 0
            delay_multiplier = 1.0
            control_state["delay_multiplier"] = delay_multiplier
            
            for i, (eid, title, entity) in enumerate(groups):
                if not control_state["running"]:
                    log.warning("Рассылка прервана пользователем.")
                    break
                    
                control_state["current_group"] = title
                
                if progress.should_skip_state(state, eid):
                    log.info("[%d/%d] ПРОПУСК (resume): %s", i + 1, total, _clean(title))
                    continue

                await ensure_active(window)
                if not control_state["running"]:
                    break
                    
                log.info("[%d/%d] Отправка в: %s (id=%d)", i + 1, total, _clean(title), eid)
                if use_tui:
                    control_state["state"] = "Отправка сообщения"

                resolved_text = resolve_spintax(text_template)
                mutated_text = mutate_message(resolved_text)
                final_text, entities = emoji.parse_custom_emoji(mutated_text)
                formatting_entities = entities if entities else None

                result = await sender.send(
                    client_obj, entity, final_text, media, formatting_entities=formatting_entities
                )

                if result.ok:
                    await _record(state, eid, progress.STATUS_SENT, result.reason)
                    control_state["sent"] += 1
                    extra = f" ({result.reason})" if result.reason else ""
                    log.info("[%d/%d] ОТПРАВЛЕНО%s.", i + 1, total, extra)
                    if result.floodwait_seconds > 0:
                        if result.floodwait_seconds < 30:
                            factor = 1.2
                        elif result.floodwait_seconds < 300:
                            factor = 1.5
                        else:
                            factor = 2.0
                        delay_multiplier = min(4.0, delay_multiplier * factor)
                        control_state["delay_multiplier"] = delay_multiplier
                        log.info(
                            "Множитель задержек увеличен до %.2fx из-за FloodWait (%d сек)",
                            delay_multiplier,
                            result.floodwait_seconds,
                        )
                    else:
                        if delay_multiplier > 1.0:
                            delay_multiplier = max(1.0, delay_multiplier - 0.1)
                            control_state["delay_multiplier"] = delay_multiplier
                            log.info("Снижаем множитель задержек до %.2fx", delay_multiplier)
                elif result.status == progress.STATUS_SKIPPED:
                    await _record(state, eid, progress.STATUS_SKIPPED, result.reason)
                    control_state["skipped"] += 1
                    log.warning("[%d/%d] ПРОПУСК: %s", i + 1, total, result.reason)
                    if result.floodwait_seconds > 0:
                        if result.floodwait_seconds < 30:
                            factor = 1.3
                        elif result.floodwait_seconds < 300:
                            factor = 1.8
                        else:
                            factor = 2.5
                        delay_multiplier = min(4.0, delay_multiplier * factor)
                        control_state["delay_multiplier"] = delay_multiplier
                        log.info(
                            "Множитель задержек увеличен до %.2fx из-за FloodWait при пропуске (%d сек)",
                            delay_multiplier,
                            result.floodwait_seconds,
                        )
                else:
                    await _record(state, eid, progress.STATUS_ERROR, result.reason)
                    control_state["errors"] += 1
                    log.error("[%d/%d] ОШИБКА: %s", i + 1, total, result.reason)

                done += 1
                if i != total - 1 and control_state["running"]:
                    if done % config.BATCH_SIZE == 0:
                        base_pause = random.randint(
                            min(config.BATCH_PAUSE_MIN_SEC, config.BATCH_PAUSE_MAX_SEC),
                            max(config.BATCH_PAUSE_MIN_SEC, config.BATCH_PAUSE_MAX_SEC),
                        )
                        pause = int(base_pause * delay_multiplier)
                        log.info(
                            "Перерыв пакета (каждые %d): %d сек (базовый %d сек, множитель %.2fx).",
                            config.BATCH_SIZE,
                            pause,
                            base_pause,
                            delay_multiplier,
                        )
                        await smart_sleep(pause, "Перерыв пакета")
                    else:
                        base_delay = random.randint(
                            min(config.DELAY_MIN_SEC, config.DELAY_MAX_SEC),
                            max(config.DELAY_MIN_SEC, config.DELAY_MAX_SEC),
                        )
                        delay = int(base_delay * delay_multiplier)
                        log.info(
                            "Задержка перед следующим: %d сек (базовый %d сек, множитель %.2fx).",
                            delay,
                            base_delay,
                            delay_multiplier,
                        )
                        await smart_sleep(delay, "Задержка перед следующим")

            log.info(progress.report())
            if use_tui:
                control_state["state"] = "Завершено"
                await asyncio.sleep(1.0)

        if client is not None:
            await execute_campaign(client)
        else:
            async with auth.client_session() as new_client:
                await execute_campaign(new_client)
    finally:
        # Очистка TUI ресурсов
        control_state["running"] = False
        if updater_task:
            updater_task.cancel()
        if listener_task:
            listener_task.cancel()
        if live:
            live.stop()
            
        # Восстановление стандартного StreamHandler логов
        for h in root.handlers:
            if isinstance(h, logging.StreamHandler) and h != tui_log_handler:
                h.setLevel(logging.INFO)
        if tui_log_handler:
            root.removeHandler(tui_log_handler)
            
        # Вывод красивого итогового отчета
        console = Console()
        console.print()
        summary_table = Table(title="[bold cyan]Итоги рекламной кампании[/]", border_style="cyan")
        summary_table.add_column("Статус выполнения", style="bold cyan")
        summary_table.add_column("Количество групп", style="bold white", justify="right")
        summary_table.add_row("Успешно отправлено", f"[bold green]{control_state['sent']}[/]")
        summary_table.add_row("Пропущено (нет прав)", f"[bold yellow]{control_state['skipped']}[/]")
        summary_table.add_row("Ошибки отправки", f"[bold red]{control_state['errors']}[/]")
        summary_table.add_row("Всего в списке", f"[bold white]{control_state['total']}[/]")
        console.print(summary_table)
        console.print("[dim]Полный лог работы сохранен в файле запуска.[/]")
        console.print()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.warning("Прервано пользователем.")


if __name__ == "__main__":
    main()
