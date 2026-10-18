"""Рассылка: ежедневная утренняя сводка и оповещения о событиях за час до начала.

Модуль не зависит от aiogram: отправка выполняется переданной функцией sender,
поэтому логику легко тестировать с заглушкой. Что уже отправлено, помнит
StateStore (JSON-файл), поэтому перезапуск бота не приводит к дублям.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import Settings
from .events import Event, detect_events, upcoming_events
from .formatting import (
    SECTION_CURRENT,
    SECTION_EVENTS,
    SECTION_FORECAST,
    SECTION_MARINE,
    SECTION_NOTES,
    format_events,
    format_report,
)
from .models import Report
from .service import ReportCache
from .subscribers import StateStore
from .textutil import split_text
from .timeutil import local_now

logger = logging.getLogger(__name__)

# Функция отправки: chat_id и текст сообщения.
Sender = Callable[[int, str], Awaitable[None]]

KIND_DAILY = "daily"
KIND_ALERT = "alert"
KIND_TEST = "test"

# Разделы ежедневной утренней сводки.
DAILY_SECTIONS = (
    SECTION_CURRENT,
    SECTION_MARINE,
    SECTION_EVENTS,
    SECTION_FORECAST,
    SECTION_NOTES,
)

MAX_TEXT_LENGTH = 3900


@dataclass(slots=True)
class Delivery:
    """Одно отправленное сообщение: кому и что именно ушло."""

    chat_id: int
    kind: str
    key: str
    text: str


class Notifier:
    """Следит за расписанием и рассылает сводку с оповещениями."""

    def __init__(
        self,
        settings: Settings,
        store: StateStore | None = None,
        sender: Sender | None = None,
        cache: ReportCache | None = None,
    ) -> None:
        self.settings = settings
        self.store = store or StateStore(
            settings.state_file, default_subscribers=settings.telegram_allowed_chat_ids
        )
        self.sender = sender
        self.cache = cache or ReportCache(settings)

    @property
    def subscribers(self) -> tuple[int, ...]:
        return self.store.subscribers()

    # ------------------------------------------------------------------
    # Один проход рассылки
    # ------------------------------------------------------------------

    async def tick(self, now: datetime | None = None, mark: bool = True) -> list[Delivery]:
        """Проверяет расписание и отправляет то, что пора отправлять.

        mark=False — сухой прогон: сообщения формируются, но состояние не меняется,
        поэтому такие прогоны не мешают реальной рассылке.
        """
        if not self.settings.notify_events_enabled:
            return []

        moment = now or local_now(self.settings.timezone)
        if not self.subscribers:
            logger.info("Рассылка включена, но подписчиков нет: добавьте /subscribe")
            return []

        try:
            report = await self.cache.get()
        except Exception as exc:  # noqa: BLE001 - рассылка не должна падать целиком
            logger.warning("Не удалось собрать сводку для рассылки: %s", exc)
            return []

        delivered: list[Delivery] = []
        if self.daily_due(moment):
            deliveries = await self._broadcast(
                KIND_DAILY, f"daily:{moment:%Y-%m-%d}", self.daily_text(report, moment)
            )
            if deliveries and mark:
                self.store.mark_daily(moment.date(), moment)
            delivered.extend(deliveries)

        for event in upcoming_events(report, self.settings, now=moment):
            if self.store.alert_sent(event.key):
                continue
            deliveries = await self._broadcast(
                KIND_ALERT, event.key, self.alert_text(event, moment)
            )
            if deliveries and mark:
                self.store.mark_alert(event.key, moment.date())
            delivered.extend(deliveries)
        return delivered

    def daily_due(self, moment: datetime) -> bool:
        """Пора ли отправлять ежедневную сводку (время из NOTIFY_DAILY_TIME)."""
        if self.store.daily_sent(moment.date()):
            return False
        midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        target = midnight + timedelta(minutes=self.settings.notify_daily_minutes)
        if moment < target:
            return False
        grace = timedelta(minutes=self.settings.notify_daily_grace_minutes)
        return moment <= target + grace

    # ------------------------------------------------------------------
    # Тексты сообщений
    # ------------------------------------------------------------------

    def events(self, report: Report, now: datetime | None = None) -> list[Event]:
        """События в окне прогноза — для команд бота и рассылки."""
        return detect_events(report, self.settings, now=now)

    def daily_text(self, report: Report, moment: datetime) -> str:
        """Утренняя сводка с разделом событий."""
        body = format_report(
            report,
            DAILY_SECTIONS,
            events=self.events(report, moment),
            zone_name=self.settings.timezone,
        )
        headline = f"☀️ Доброе утро! Сводка на {moment:%d.%m.%Y}"
        sun_line = _sun_line(report)
        parts = [headline]
        if sun_line:
            parts.append(sun_line)
        parts.append(body)
        return _fit("\n\n".join(parts))

    def alert_text(self, event: Event, moment: datetime) -> str:
        """Оповещение о событии: что, когда и через сколько."""
        minutes = int((event.starts_at - moment).total_seconds() // 60)
        if event.instantaneous:
            lead = (
                "произошло только что" if minutes <= 1 else f"{abs(minutes)} мин назад"
            )
        elif minutes <= 1:
            lead = "начинается сейчас"
        else:
            lead = f"через {minutes} мин"
        return _fit(f"{event.headline(self.settings.timezone)}\n\n{event.summary} — {lead}.")

    def events_text(self, report: Report, moment: datetime) -> str:
        """Список событий для команды /events."""
        return format_events(self.events(report, moment), self.settings.timezone)

    # ------------------------------------------------------------------
    # Отправка
    # ------------------------------------------------------------------

    async def _broadcast(self, kind: str, key: str, text: str) -> list[Delivery]:
        """Отправляет текст всем подписчикам; собирает успешные доставки."""
        delivered: list[Delivery] = []
        for chat_id in self.subscribers:
            try:
                await self._send(chat_id, text)
            except Exception as exc:  # noqa: BLE001 - один чат не должен ломать рассылку
                logger.warning("Не удалось отправить в chat_id=%s: %s", chat_id, exc)
                continue
            delivered.append(Delivery(chat_id=chat_id, kind=kind, key=key, text=text))
        return delivered

    async def _send(self, chat_id: int, text: str) -> None:
        """Отправляет сообщение: заданным sender или в лог (сухой режим)."""
        if self.sender is None:
            logger.info("[сухой режим] chat_id=%s: %s", chat_id, text.splitlines()[0])
            return
        for chunk in split_text(text):
            await self.sender(chat_id, chunk)

    async def send_test(self) -> list[Delivery]:
        """Отправляет короткое проверочное сообщение (для --notify-test)."""
        moment = local_now(self.settings.timezone)
        text = (
            "🔔 Проверка связи: рассылка работает.\n"
            f"{self.settings.city}, {moment:%d.%m.%Y %H:%M} "
            f"({self.settings.timezone_label})."
        )
        return await self._broadcast(KIND_TEST, f"test:{moment:%Y-%m-%d}", text)

    async def run(self, stop: asyncio.Event | None = None) -> None:
        """Цикл рассылки: раз в NOTIFY_POLL_SECONDS проверяет расписание."""
        interval = max(15, self.settings.notify_poll_seconds)
        logger.info(
            "Рассылка запущена: сводка в %s, оповещения за %d мин, опрос каждые %d с",
            self.settings.notify_daily_time,
            self.settings.notify_event_lead_minutes,
            interval,
        )
        while stop is None or not stop.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - цикл должен пережить любую ошибку
                logger.exception("Ошибка в цикле рассылки")
            if stop is None:
                await asyncio.sleep(interval)
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                continue


def _sun_line(report: Report) -> str | None:
    """Отдельная строка 'Восход … · закат …' из первого дня прогноза.

    Живёт вне блока «Прогноз по дням», поэтому появляется в утренней
    сводке независимо от того, отрендерился ли этот блок.
    """
    days = getattr(report, "days", None) or []
    if not days:
        return None
    first = days[0]
    sunrise = getattr(first, "sunrise", None)
    sunset = getattr(first, "sunset", None)
    if not (sunrise and sunset):
        return None
    return f"🌅 Восход {sunrise} · закат {sunset}"


def _fit(text: str, limit: int = MAX_TEXT_LENGTH) -> str:
    """Обрезает текст до лимита одного сообщения."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def build_notifier(
    settings: Settings,
    sender: Sender | None = None,
    cache: ReportCache | None = None,
    store: StateStore | None = None,
) -> Notifier:
    """Собирает Notifier с хранилищем состояния из настроек."""
    return Notifier(
        settings,
        store=store
        or StateStore(
            settings.state_file, default_subscribers=settings.telegram_allowed_chat_ids
        ),
        sender=sender,
        cache=cache,
    )
