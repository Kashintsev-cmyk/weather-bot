"""Проверки рассылки: расписание утренней сводки, дедупликация и живучесть."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

from app.config import Settings
from app.notify import KIND_ALERT, KIND_DAILY, KIND_TEST, build_notifier
from app.subscribers import KEEP_DAYS, StateStore
from helpers import hours, report

NOW = datetime(2026, 9, 30, 7, 30, tzinfo=timezone(timedelta(hours=3)))
# Часы прогноза в тестах строим от начала часа, чтобы окна совпадали с 08:00.
HOUR_BASE = NOW.replace(minute=0, second=0, microsecond=0)


def await_tick(notifier, moment):
    """Синхронный вызов одного прохода рассылки (без pytest-asyncio)."""
    return asyncio.run(notifier.tick(now=moment))



def _settings(tmp_path, **overrides) -> Settings:
    """Настройки с файлом состояния внутри tmp_path."""
    params = dict(
        city="Адлер",
        timezone="Europe/Moscow",
        telegram_allowed_chat_ids=(111, 222),
        state_file=tmp_path / "state.json",
    )
    params.update(overrides)
    return Settings(**params)


class StubCache:
    """Кэш сводки без походов в сеть."""

    def __init__(self, prepared) -> None:
        self.prepared = prepared
        self.calls = 0

    async def get(self, force: bool = False):  # noqa: ANN201 - тест
        self.calls += 1
        return self.prepared


class Sender:
    """Заглушка отправки: запоминает сообщения и умеет падать для части чатов."""

    def __init__(self, failing: tuple[int, ...] = ()) -> None:
        self.failing = failing
        self.messages: list[tuple[int, str]] = []

    async def __call__(self, chat_id: int, text: str) -> None:
        if chat_id in self.failing:
            raise RuntimeError("Telegram недоступен")
        self.messages.append((chat_id, text))


def _rainy_report(settings: Settings):
    """Сводка с дождём в 08:00 — событие для оповещения."""
    built = hours(
        HOUR_BASE,
        [
            {"shift": 1, "precipitation": 1.0, "probability": 90, "weather_code": 61},
            {"shift": 2, "probability": 30},
        ],
    )
    return report(settings, built, now=HOUR_BASE)


def test_state_store_round_trip(tmp_path) -> None:
    """Подписки и метки отправки переживают перезапуск."""
    path = tmp_path / "state.json"
    store = StateStore(path, default_subscribers=(111,))
    assert store.subscribers() == (111,)

    assert store.subscribe(222) is True
    assert store.subscribe(222) is False
    store.mark_daily(date(2026, 9, 30), NOW)
    store.mark_alert("rain:2026093008", date(2026, 9, 30))

    reloaded = StateStore(path, default_subscribers=(999,))
    assert reloaded.subscribers() == (111, 222)
    assert reloaded.daily_sent(date(2026, 9, 30))
    assert reloaded.alert_sent("rain:2026093008")
    assert not reloaded.alert_sent("thunder:2026093009")
    assert reloaded.unsubscribe(111) is True
    assert reloaded.unsubscribe(111) is False
    assert StateStore(path).subscribers() == (222,)


def test_state_store_ignores_broken_file(tmp_path) -> None:
    """Битый файл состояния не роняет бота: берём подписки по умолчанию."""
    path = tmp_path / "state.json"
    path.write_text("{это не json", encoding="utf-8")

    store = StateStore(path, default_subscribers=(777,))

    assert store.subscribers() == (777,)
    assert store.state.daily == {}


def test_state_store_prunes_old_records(tmp_path) -> None:
    """История старше KEEP_DAYS удаляется при сохранении."""
    path = tmp_path / "state.json"
    store = StateStore(path, default_subscribers=(1,))
    old_day = date(2026, 9, 30) - timedelta(days=KEEP_DAYS + 2)
    store.mark_alert("rain:old", old_day)
    store.mark_daily(old_day, NOW)

    store.save()

    assert not store.alert_sent("rain:old")
    assert not store.daily_sent(old_day)
    assert store.subscribers() == (1,)


def test_daily_due_window(tmp_path) -> None:
    """Сводка уходит только один раз в сутки и только в окно допуска."""
    settings = _settings(tmp_path, notify_daily_time="07:30")
    notifier = build_notifier(settings, cache=StubCache(None))

    assert not notifier.daily_due(NOW.replace(hour=7, minute=29))
    assert notifier.daily_due(NOW)
    assert notifier.daily_due(NOW.replace(hour=10, minute=29))
    assert not notifier.daily_due(NOW.replace(hour=10, minute=31))

    notifier.store.mark_daily(NOW.date())
    assert not notifier.daily_due(NOW)


def _calm_report(settings: Settings):
    """Сводка без событий: проверяем только утреннюю рассылку."""
    built = hours(
        HOUR_BASE,
        [
            {"shift": shift, "temperature": 21.0, "weather_code": 0, "probability": 5}
            for shift in range(1, 8)
        ],
    )
    return report(settings, built, now=HOUR_BASE)


def test_daily_summary_is_sent_once_to_all_subscribers(tmp_path) -> None:
    """Утренняя сводка уходит всем подписчикам ровно один раз в сутки."""
    settings = _settings(tmp_path)
    sender = Sender()
    notifier = build_notifier(settings, sender=sender, cache=StubCache(_calm_report(settings)))

    first = await_tick(notifier, NOW)
    second = await_tick(notifier, NOW + timedelta(minutes=5))

    assert [item.kind for item in first] == [KIND_DAILY, KIND_DAILY]
    assert [item.chat_id for item in first] == [111, 222]
    assert first[0].key == "daily:2026-09-30"
    assert "Доброе утро! Сводка на 30.09.2026" in first[0].text
    assert "События: опасных явлений в ближайшем прогнозе не ожидается." in first[0].text
    assert second == []
    assert len(sender.messages) == 2
    assert notifier.store.daily_sent(NOW.date())


def test_alert_is_sent_once_and_marked(tmp_path) -> None:
    """Оповещение о дожде уходит за час и не повторяется на следующем проходе."""
    settings = _settings(tmp_path, notify_daily_time="12:00")
    sender = Sender()
    notifier = build_notifier(
        settings, sender=sender, cache=StubCache(_rainy_report(settings))
    )

    first = await_tick(notifier, NOW)
    second = await_tick(notifier, NOW + timedelta(minutes=30))

    assert [item.kind for item in first] == [KIND_ALERT, KIND_ALERT]
    assert first[0].key == "rain:2026093008"
    assert "Дождь · 08:00–09:00" in first[0].text
    assert "Ожидается дождь — через 30 мин." in first[0].text
    assert second == []
    assert notifier.store.alert_sent("rain:2026093008")


def test_failed_chat_does_not_block_others(tmp_path) -> None:
    """Ошибка отправки в один чат не мешает остальным и не теряет метку."""
    settings = _settings(tmp_path, notify_daily_time="12:00")
    sender = Sender(failing=(111,))
    notifier = build_notifier(
        settings, sender=sender, cache=StubCache(_rainy_report(settings))
    )

    delivered = await_tick(notifier, NOW)

    assert [item.chat_id for item in delivered] == [222]
    assert [chat_id for chat_id, _ in sender.messages] == [222]
    assert notifier.store.alert_sent("rain:2026093008")


def test_without_subscribers_nothing_is_fetched(tmp_path) -> None:
    """Без подписчиков рассылка не дёргает площадки."""
    settings = _settings(tmp_path, telegram_allowed_chat_ids=())
    cache = StubCache(_rainy_report(settings))
    notifier = build_notifier(settings, sender=Sender(), cache=cache)

    assert await_tick(notifier, NOW) == []
    assert cache.calls == 0


def test_notifications_can_be_disabled(tmp_path) -> None:
    """NOTIFY_EVENTS_ENABLED=false выключает и сводку, и оповещения."""
    settings = _settings(tmp_path, notify_events_enabled=False)
    notifier = build_notifier(settings, sender=Sender(), cache=StubCache(None))

    assert await_tick(notifier, NOW) == []


def test_dry_run_does_not_change_state(tmp_path) -> None:
    """Сухой прогон без sender ничего не отправляет и не трогает состояние."""
    settings = _settings(tmp_path)
    notifier = build_notifier(settings, cache=StubCache(_rainy_report(settings)))

    delivered = asyncio.run(notifier.tick(now=NOW, mark=False))

    assert [item.kind for item in delivered] == [
        KIND_DAILY,
        KIND_DAILY,
        KIND_ALERT,
        KIND_ALERT,
    ]
    assert not notifier.store.daily_sent(NOW.date())
    assert not notifier.store.alert_sent("rain:2026093008")


def test_send_test_reaches_subscribers(tmp_path) -> None:
    """Проверочное сообщение уходит подписчикам и не влияет на метки событий."""
    settings = _settings(tmp_path)
    sender = Sender()
    notifier = build_notifier(settings, sender=sender, cache=StubCache(None))

    delivered = asyncio.run(notifier.send_test())

    assert [item.kind for item in delivered] == [KIND_TEST, KIND_TEST]
    assert "Проверка связи" in delivered[0].text
    assert notifier.store.state.alerts == {}


def test_events_text_lists_detected_events(tmp_path) -> None:
    """Список для команды /events берётся из детектора."""
    settings = _settings(tmp_path)
    notifier = build_notifier(settings, cache=StubCache(None))

    text = notifier.events_text(_rainy_report(settings), NOW)

    assert "Дождь · 08:00–09:00" in text
    assert notifier.events_text(_calm_report(settings), NOW) == (
        "Событий в ближайшем прогнозе нет."
    )

