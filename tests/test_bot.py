"""Проверки вспомогательных функций телеграм-бота (без обращения к Telegram)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings

aiogram = pytest.importorskip("aiogram", reason="aiogram нужен для проверки бота")

from app.bot import (  # noqa: E402
    EVENT_SECTIONS,
    FULL_SECTIONS,
    MAX_MESSAGE_LENGTH,
    SEA_SECTIONS,
    WEATHER_SECTIONS,
    build_keyboard,
    create_dispatcher,
)
from app.formatting import (  # noqa: E402
    SECTION_CURRENT,
    SECTION_EVENTS,
    SECTION_FORECAST,
    SECTION_MARINE,
    SECTION_SEA_FORECAST,
)
from app.service import ReportCache  # noqa: E402


def test_section_presets_cover_expected_blocks() -> None:
    assert SECTION_CURRENT in WEATHER_SECTIONS
    assert SECTION_FORECAST in WEATHER_SECTIONS
    assert SECTION_MARINE in SEA_SECTIONS
    assert SECTION_SEA_FORECAST in SEA_SECTIONS
    assert SECTION_EVENTS in EVENT_SECTIONS
    assert set(FULL_SECTIONS).issuperset(set(WEATHER_SECTIONS) | set(SEA_SECTIONS))


def test_help_text_mentions_subscription_commands() -> None:
    from app.bot import HELP_TEXT

    for command in ("/events", "/subscribe", "/unsubscribe", "/status"):
        assert command in HELP_TEXT


def test_main_starts_polling_once() -> None:
    """main() не должен запускать polling дважды: иначе SIGTERM не остановит бота."""
    import app.bot as bot_module

    source = Path(bot_module.__file__).read_text(encoding="utf-8")

    assert source.count("start_polling(") == 1
    assert source.count("Бот запущен") == 1


def test_split_keeps_all_lines_and_limit() -> None:
    """Текст режется по лимиту Telegram, но не теряет строки."""
    lines = [f"Строка {index} " + "х" * 40 for index in range(200)]
    text = "\n".join(lines)

    from app.bot import _split

    chunks = _split(text, limit=500)

    assert all(len(chunk) <= 500 for chunk in chunks)
    restored = [line for chunk in chunks for line in chunk.splitlines()]
    assert restored == lines


def test_split_short_text_is_single_chunk() -> None:
    from app.bot import _split

    assert _split("короткий текст") == ["короткий текст"]
    assert MAX_MESSAGE_LENGTH < 4096


def test_keyboard_has_three_buttons() -> None:
    keyboard = build_keyboard()

    callbacks = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
    ]
    assert callbacks == ["report:weather", "report:sea", "report:full"]


def test_dispatcher_carries_settings_and_cache(tmp_path) -> None:
    settings = Settings(
        city="Адлер",
        telegram_allowed_chat_ids=(123,),
        state_file=tmp_path / "state.json",
    )
    cache = ReportCache(settings)

    dispatcher = create_dispatcher(settings, cache)

    assert dispatcher["settings"] is settings
    assert dispatcher["cache"] is cache
    assert dispatcher["notifier"].cache is cache
    assert dispatcher["store"] is dispatcher["notifier"].store
    assert dispatcher["store"].subscribers() == (123,)


def test_allowed_chat_whitelist() -> None:
    open_settings = Settings()
    restricted = Settings(telegram_allowed_chat_ids=(1, 2))

    assert open_settings.is_allowed_chat(999)
    assert restricted.is_allowed_chat(2)
    assert not restricted.is_allowed_chat(3)


def test_report_cache_reuses_value() -> None:
    """Второй запрос берёт сводку из кэша, не дёргая площадки повторно."""
    import asyncio

    from app.aggregate import build_report
    from app import service
    from app.models import CurrentWeather, SourceResult

    settings = Settings(city="Адлер")
    calls = {"count": 0}

    async def fake_fetch(settings: Settings):  # noqa: ANN202 - тест
        calls["count"] += 1
        result = SourceResult(
            source="Тест",
            current=CurrentWeather(source="Тест", temperature=20.0, description="ясно"),
        )
        return build_report(settings, [result])

    cache = ReportCache(settings, ttl_seconds=300)
    original = service.fetch_report
    service.fetch_report = fake_fetch  # type: ignore[assignment]

    async def scenario():  # noqa: ANN202 - тест
        first = await cache.get()
        second = await cache.get()
        forced = await cache.get(force=True)
        return first, second, forced

    try:
        first, second, forced = asyncio.run(scenario())
    finally:
        service.fetch_report = original  # type: ignore[assignment]

    assert calls["count"] == 2
    assert first is second
    assert forced is not first
