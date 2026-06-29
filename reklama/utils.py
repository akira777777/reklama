"""Утилиты проекта: общие функции, используемые несколькими модулями."""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def setup_logging(log_name: str) -> Path:
    """Настраивает логирование в консоль + в logs/<log_name>.log с ротацией.

    Использует RotatingFileHandler: файлы до 10MB, хранит последние 5 версий.
    Возвращает путь к текущему файлу лога.
    """
    logs_dir = config.BASE_DIR / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / f"{log_name}.log"
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        if hasattr(sh.stream, "reconfigure"):
            sh.stream.reconfigure(encoding="utf-8")
        fh = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        root.addHandler(sh)
        root.addHandler(fh)
    return log_file


def clean_control_chars(title: str) -> str:
    """Удаляет управляющие символы ASCII из строки для безопасного вывода."""
    return _CONTROL_RE.sub(" ", str(title))


def mutate_message(text: str) -> str:
    """Добавляет случайные невидимые символы внутрь сообщения и хвост в конец.

    Это делает хэш каждого отправленного сообщения уникальным для обхода
    сигнатурных спам-фильтров Telegram, оставаясь невидимым для пользователей.
    Безопасно обходит теги [emoji:doc_id] для сохранения их работоспособности.
    """
    import random

    if not config.MUTATE_MESSAGE or not text:
        return text

    invisible_chars = ["\u200b", "\u200c", "\u200d", "\ufeff"]

    # Разделяем текст по тегам [emoji:doc_id], чтобы не менять их содержимое
    pattern = re.compile(r"(\[emoji:\d+\])")
    parts = pattern.split(text)

    for i in range(len(parts)):
        # Четные индексы в parts — это обычный текст, нечетные — теги эмодзи
        if i % 2 == 0 and parts[i]:
            segment = parts[i]
            mutated_segment = []
            for char in segment:
                mutated_segment.append(char)
                # С вероятностью 15% добавляем невидимый символ после пробелов
                if char.isspace() and random.random() < 0.15:
                    mutated_segment.append(random.choice(invisible_chars))
            parts[i] = "".join(mutated_segment)

    suffix = "".join(random.choices(invisible_chars, k=random.randint(1, 5)))
    return "".join(parts) + suffix
