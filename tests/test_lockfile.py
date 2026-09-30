"""Проверки защиты от одновременного запуска двух ботов."""

from __future__ import annotations

import os

import pytest

from app.lockfile import AlreadyRunningError, acquire_lock


def test_lock_is_exclusive(tmp_path) -> None:
    """Второй экземпляр не может взять блокировку, пока первый держит файл."""
    path = tmp_path / "bot.lock"
    first = acquire_lock(path)
    try:
        assert f"pid={os.getpid()}" in path.read_text(encoding="utf-8")
        with pytest.raises(AlreadyRunningError) as error:
            acquire_lock(path)
        assert "Бот уже запущен" in str(error.value)
        assert str(os.getpid()) in str(error.value)
    finally:
        first.close()

    second = acquire_lock(path)
    second.close()


def test_lock_creates_parent_directory(tmp_path) -> None:
    """Каталог для файла блокировки создаётся автоматически."""
    path = tmp_path / "state" / "bot.lock"
    handle = acquire_lock(path)
    try:
        assert path.exists()
    finally:
        handle.close()
