"""Общие фикстуры pytest: путь к проекту и настройки точки прогноза."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import Settings  # noqa: E402


@pytest.fixture()
def settings(tmp_path) -> Settings:
    """Настройки Адлера; ключ OWM фиктивный — реальных запросов в тестах нет.

    Файл состояния указывает в tmp_path, чтобы тесты не читали и не портили
    рабочий state/bot_state.json.
    """
    return Settings(
        city="Адлер",
        lat=43.427292,
        lon=39.917107,
        timezone="Europe/Moscow",
        forecast_days=3,
        openweather_api_key="test-key",
        state_file=tmp_path / "state.json",
    )
