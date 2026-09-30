"""Защита от одновременного запуска двух ботов на одном токене.

Два процесса с одним токеном конфликтуют в getUpdates (Telegram отвечает 409),
поэтому второй экземпляр должен падать сразу с понятным сообщением.
"""

from __future__ import annotations

import fcntl
import logging
import os
from pathlib import Path
from typing import IO

logger = logging.getLogger(__name__)


class AlreadyRunningError(RuntimeError):
    """Бот уже запущен: параллельный опрос Telegram недопустим."""


def acquire_lock(path: Path) -> IO[str]:
    """Берёт эксклюзивную блокировку файла и пишет туда свой pid.

    Блокировка снимается автоматически при закрытии файла или смерти процесса.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        owner = _read_owner(path)
        handle.close()
        suffix = f" (pid {owner})" if owner else ""
        raise AlreadyRunningError(
            f"Бот уже запущен{suffix}: блокировка {path}. Остановите другой экземпляр."
        ) from exc

    handle.seek(0)
    handle.truncate()
    handle.write(f"pid={os.getpid()}\n")
    handle.flush()
    logger.debug("Блокировка одиночного запуска получена: %s", path)
    return handle


def _read_owner(path: Path) -> str | None:
    """Пытается прочитать pid владельца блокировки (для понятного сообщения)."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text.removeprefix("pid=") or None
