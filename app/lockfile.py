"""Защита от одновременного запуска двух ботов на одном токене.

Два процесса с одним токеном конфликтуют в getUpdates (Telegram отвечает 409),
поэтому второй экземпляр не должен доходить до опроса. Короткая пауза в ожидании
блокировки нужна для перезапусков: systemd может запустить новый процесс, пока
старый ещё не освободил файл.
"""

from __future__ import annotations

import fcntl
import logging
import os
import time
from pathlib import Path
from typing import IO

logger = logging.getLogger(__name__)

LOCK_WAIT_SECONDS = 15.0
LOCK_POLL_SECONDS = 0.25


class AlreadyRunningError(RuntimeError):
    """Бот уже запущен: параллельный опрос Telegram недопустим."""


def acquire_lock(
    path: Path,
    wait_seconds: float = LOCK_WAIT_SECONDS,
    poll_seconds: float = LOCK_POLL_SECONDS,
) -> IO[str]:
    """Берёт эксклюзивную блокировку файла и пишет туда свой pid.

    Если файл занят, ждём освобождения до ``wait_seconds`` секунд (перезапуск по
    SIGTERM занимает меньше секунды, поэтому `systemctl restart` проходит без
    ошибок), и только потом падаем. Блокировка снимается при закрытии файла или
    смерти процесса; сам файл остаётся и хранит pid последнего владельца.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    deadline = time.monotonic() + max(wait_seconds, 0.0)
    waiting = False
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if time.monotonic() >= deadline:
                owner = _read_owner(path)
                handle.close()
                suffix = f" (pid {owner})" if owner else ""
                raise AlreadyRunningError(
                    f"Бот уже запущен{suffix}: блокировка {path}. Остановите другой экземпляр."
                ) from exc
            if not waiting:
                logger.info(
                    "Блокировка %s занята, ждём освобождения до %.0f с", path, wait_seconds
                )
                waiting = True
            time.sleep(max(poll_seconds, 0.0))
            continue

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
