"""Бесконечная ротация аккаунтов: каждый аккаунт проходит полный цикл рассылки по очереди.

Схема работы:
    Цикл #1: Аккаунт reklama  → рассылка по всем группам → готово
    Цикл #2: Аккаунт reklama2 → рассылка по всем группам → готово
    Цикл #3: Аккаунт reklama  → снова с начала → ...
    (бесконечно до ручной остановки)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger("rotator")


class AccountRotator:
    """Управляет бесконечным чередованием аккаунтов."""

    def __init__(self) -> None:
        self.state: dict[str, Any] = {
            "running": False,
            "current_account": "",
            "cycle_number": 0,
            "cycles_by_account": {},
            "status": "idle",          # idle | running | switching | stopping | stopped
            "pause_between_sec": 60,
            "reset_each_cycle": True,
        }
        self._stop_event: asyncio.Event = asyncio.Event()

    def reset(
        self,
        pause_between_sec: int = 60,
        reset_each_cycle: bool = True,
    ) -> None:
        self._stop_event.clear()
        self.state.update(
            {
                "running": True,
                "current_account": "",
                "cycle_number": 0,
                "cycles_by_account": {},
                "status": "running",
                "pause_between_sec": pause_between_sec,
                "reset_each_cycle": reset_each_cycle,
            }
        )

    def stop(self) -> None:
        self.state["running"] = False
        self.state["status"] = "stopping"
        self._stop_event.set()
        log.info("Ротация: получен сигнал остановки.")

    async def run(
        self,
        accounts_with_clients: list[tuple],  # list of (Account, TelegramClient)
        reset_each_cycle: bool = True,
        pause_between_sec: int = 60,
    ) -> None:
        """Главный цикл ротации. Запускается как asyncio.Task."""
        # Импортируем здесь, чтобы избежать циклических зависимостей
        import run as run_module
        from reklama import progress as progress_module

        self.reset(pause_between_sec=pause_between_sec, reset_each_cycle=reset_each_cycle)

        if not accounts_with_clients:
            log.error("Ротация: список аккаунтов пуст. Завершаем.")
            self.state["running"] = False
            self.state["status"] = "stopped"
            return

        log.info(
            "═══ Запуск бесконечной ротации аккаунтов ═══ "
            "Аккаунтов: %d | Сброс прогресса: %s | Пауза между сменой: %d сек",
            len(accounts_with_clients),
            reset_each_cycle,
            pause_between_sec,
        )

        # Инициализируем счётчики циклов для каждого аккаунта
        for account, _client in accounts_with_clients:
            self.state["cycles_by_account"][account.name] = 0

        global_cycle = 0

        while self.state["running"]:
            for account, client in accounts_with_clients:
                if not self.state["running"]:
                    break

                global_cycle += 1
                self.state["cycle_number"] = global_cycle
                self.state["current_account"] = account.name
                self.state["status"] = "running"

                log.info(
                    "╔══════════════════════════════════════════╗\n"
                    "║  Цикл #%d | Аккаунт: %-20s  ║\n"
                    "╚══════════════════════════════════════════╝",
                    global_cycle,
                    account.name,
                )

                # Сброс прогресса для данного аккаунта (чтобы рассылка шла с начала)
                if reset_each_cycle:
                    progress_module.reset(account.progress_path)
                    log.info("Прогресс аккаунта «%s» сброшен для нового цикла.", account.name)

                # Сброс движка перед новым циклом
                run_module.engine.reset()

                try:
                    await run_module.run(
                        client=client,
                        dry_run=False,
                        limit=None,
                        reset_progress=False,   # сброс уже сделан выше вручную
                        no_tui=True,
                        account=account,
                    )
                except Exception as exc:
                    log.error(
                        "Цикл #%d (аккаунт «%s») завершился с ошибкой: %s",
                        global_cycle,
                        account.name,
                        exc,
                        exc_info=True,
                    )

                self.state["cycles_by_account"][account.name] = (
                    self.state["cycles_by_account"].get(account.name, 0) + 1
                )

                log.info(
                    "Цикл #%d аккаунта «%s» завершён. "
                    "Итого циклов этого аккаунта: %d.",
                    global_cycle,
                    account.name,
                    self.state["cycles_by_account"][account.name],
                )

                # Если это не последний аккаунт в раунде — делаем паузу перед следующим
                if self.state["running"] and pause_between_sec > 0:
                    self.state["status"] = "switching"
                    log.info(
                        "Пауза между аккаунтами: %d сек. Следующий аккаунт стартует через %d сек.",
                        pause_between_sec,
                        pause_between_sec,
                    )
                    # Ожидаем с возможностью прерывания
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(self._stop_event.wait()),
                            timeout=float(pause_between_sec),
                        )
                        # Если сюда дошли — значит stop() вызван во время паузы
                        break
                    except asyncio.TimeoutError:
                        pass  # Пауза закончилась штатно — продолжаем

        self.state["running"] = False
        self.state["status"] = "stopped"
        self.state["current_account"] = ""
        log.info(
            "═══ Ротация остановлена. Всего выполнено циклов: %d ═══",
            global_cycle,
        )


# Глобальный экземпляр ротатора (используется из web.py)
rotator = AccountRotator()
