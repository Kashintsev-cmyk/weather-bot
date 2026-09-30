"""Проверки защиты от одновременного запуска двух ботов."""

from __future__ import annotations

import os
import threading
import time

import pytest

from app.lockfile import AlreadyRunningError, acquire_lock


def test_lock_is_exclusive(tmp_path) -> None:
    """Второй экземпляр не может взять блокировку, пока первый держит файл."""
    path = tmp_path / "bot.lock"
    first = acquire_lock(path)
    try:
        assert f"pid={os.getpid()}" in path.read_text(encoding="utf-8")
        with pytest.raises(AlreadyRunningError) as error:
            acquire_lock(path, wait_seconds=0.0)
        assert "Бот уже запущен" in str(error.value)
        assert str(os.getpid()) in str(error.value)
    finally:
        first.close()

    second = acquire_lock(path, wait_seconds=0.0)
    second.close()


def test_lock_waits_until_release(tmp_path) -> None:
    """Перезапуск не падает: бот ждёт, пока предыдущий экземпляр отдаст блокировку."""
    path = tmp_path / "bot.lock"
    first = acquire_lock(path)
    released: list[str] = []

    def release() -> None:
        time.sleep(0.3)
        first.close()
        released.append("released")

    thread = threading.Thread(target=release)
    thread.start()
    try:
        second = acquire_lock(path, wait_seconds=5.0, poll_seconds=0.05)
    finally:
        thread.join()

    try:
        assert released == ["released"]
    finally:
        second.close()


def test_lock_gives_up_after_wait(tmp_path) -> None:
    """Если блокировка так и не освободилась, экземпляр падает с понятной ошибкой."""
    path = tmp_path / "bot.lock"
    first = acquire_lock(path)
    try:
        started = time.monotonic()
        with pytest.raises(AlreadyRunningError):
            acquire_lock(path, wait_seconds=0.3, poll_seconds=0.05)
        assert time.monotonic() - started >= 0.3
    finally:
        first.close()


def test_lock_file_keeps_last_owner_pid(tmp_path) -> None:
    """Файл блокировки не удаляется: в нём остаётся pid последнего владельца."""
    path = tmp_path / "bot.lock"
    handle = acquire_lock(path, wait_seconds=0.0)
    handle.close()

    assert path.exists()
    assert path.read_text(encoding="utf-8").strip() == f"pid={os.getpid()}"


def test_lock_creates_parent_directory(tmp_path) -> None:
    """Каталог для файла блокировки создаётся автоматически."""
    path = tmp_path / "state" / "bot.lock"
    handle = acquire_lock(path)
    try:
        assert path.exists()
    finally:
        handle.close()
