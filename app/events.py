"""Детектор событий: превращает обобщённую сводку в список предупреждений.

Модуль не знает ни о Telegram, ни о расписании: на входе Report и настройки,
на выходе список Event, отсортированный по времени начала. Рассылка
(app/notify.py) сама решает, что и когда отправлять.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from .codes import wmo_is_fog, wmo_is_hail, wmo_is_heavy, wmo_is_snow, wmo_is_thunder
from .config import Settings
from .models import AggregatedHour, AggregatedMarineHour, Report
from .timeutil import as_local, local_now

# Уровни важности: чем больше число, тем выше приоритет в сообщении.
LEVEL_INFO = 1
LEVEL_WARNING = 2
LEVEL_DANGER = 3

LEVEL_LABELS = {
    LEVEL_INFO: "info",
    LEVEL_WARNING: "warning",
    LEVEL_DANGER: "danger",
}
LEVEL_ICONS = {
    LEVEL_INFO: "ℹ️",
    LEVEL_WARNING: "⚠️",
    LEVEL_DANGER: "🔴",
}

# Типы событий (первый компонент ключа дедупликации).
KIND_RAIN = "rain"
KIND_SNOW = "snow"
KIND_THUNDER = "thunder"
KIND_HAIL = "hail"
KIND_SQUALL = "squall"
KIND_HURRICANE = "hurricane"
KIND_TORNADO = "tornado"
KIND_FOG = "fog"
KIND_HEAT = "heat"
KIND_FROST = "frost"
KIND_COLD_SNAP = "cold_snap"
KIND_MARINE = "marine"
KIND_MARINE_STORM = "marine_storm"
KIND_QUAKE = "quake"
KIND_TSUNAMI = "tsunami"

KIND_TITLES = {
    KIND_RAIN: "Дождь",
    KIND_SNOW: "Снег",
    KIND_THUNDER: "Гроза",
    KIND_HAIL: "Град",
    KIND_SQUALL: "Сильный ветер",
    KIND_HURRICANE: "Ураганный ветер",
    KIND_TORNADO: "Шквалы и смерчи",
    KIND_FOG: "Туман",
    KIND_HEAT: "Жара",
    KIND_FROST: "Заморозки",
    KIND_COLD_SNAP: "Резкое похолодание",
    KIND_MARINE: "Сильное волнение",
    KIND_MARINE_STORM: "Шторм на море",
    KIND_QUAKE: "Землетрясение",
    KIND_TSUNAMI: "Риск цунами",
}

# Типы, которые считаем снежными при отсутствии явного кода снега.
SNOW_KINDS = ("snow", "sleet")
# Максимальный разрыв между часами внутри одного события.
EPISODE_GAP = timedelta(hours=3, minutes=30)
MARINE_GAP = timedelta(hours=6)
HEAVY_RAIN_MM_PER_HOUR = 5.0
# Насколько свежим должно быть землетрясение, чтобы попасть в оповещение.
QUAKE_RECENT_HOURS = 24.0
QUAKE_DEPTH_SHALLOW_KM = 70.0


@dataclass(slots=True)
class Event:
    """Одно предупреждение: что, когда и насколько опасно."""

    kind: str
    level: int
    starts_at: datetime
    ends_at: datetime
    summary: str
    details: str = ""
    sources: tuple[str, ...] = ()

    @property
    def title(self) -> str:
        return KIND_TITLES.get(self.kind, self.kind)

    @property
    def level_label(self) -> str:
        return LEVEL_LABELS.get(self.level, "info")

    @property
    def instantaneous(self) -> bool:
        """Моментальное событие (землетрясение): окно не показываем."""
        return self.ends_at <= self.starts_at

    @property
    def key(self) -> str:
        """Стабильный ключ для дедупликации: тип и час начала."""
        return f"{self.kind}:{self.starts_at:%Y%m%d%H}"

    @property
    def sort_key(self) -> tuple[datetime, int]:
        return (self.starts_at, -self.level)

    def window_label(self, zone_name: str) -> str:
        """Человекочитаемое окно события в часовом поясе точки."""
        start = as_local(self.starts_at, zone_name)
        if self.instantaneous:
            return start.strftime("%H:%M")
        end = as_local(self.ends_at, zone_name)
        if start.date() == end.date():
            return f"{start:%H:%M}–{end:%H:%M}"
        return f"{start:%d.%m %H:%M}–{end:%d.%m %H:%M}"

    def headline(self, zone_name: str) -> str:
        """Короткая строка для списка событий и уведомления."""
        icon = LEVEL_ICONS.get(self.level, "ℹ️")
        text = f"{icon} {self.title} · {self.window_label(zone_name)}"
        if self.details:
            text = f"{text}\n{self.details}"
        return text


def detect_events(
    report: Report,
    settings: Settings,
    now: datetime | None = None,
) -> list[Event]:
    """Возвращает все события в окне прогноза, ближайшие сверху."""
    moment = now or local_now(settings.timezone)
    horizon = moment + timedelta(hours=settings.notify_event_horizon_hours)
    hours = _window_hours(report, moment, horizon)

    events: list[Event] = []
    events.extend(_precipitation_events(hours, settings))
    events.extend(_wind_events(hours, settings))
    events.extend(_fog_events(hours, settings))
    events.extend(_temperature_events(hours, settings))
    events.extend(_marine_events(report, settings, moment, horizon))
    events.extend(_quake_events(report, settings, moment))
    events.sort(key=lambda event: event.sort_key)
    return events


def upcoming_events(
    report: Report,
    settings: Settings,
    now: datetime | None = None,
    lead_minutes: int | None = None,
) -> list[Event]:
    """События, начинающиеся не позже чем через lead_minutes минут."""
    moment = now or local_now(settings.timezone)
    lead = timedelta(
        minutes=settings.notify_event_lead_minutes if lead_minutes is None else lead_minutes
    )
    limit = moment + lead
    result: list[Event] = []
    for event in detect_events(report, settings, now=moment):
        if event.starts_at > limit:
            continue
        if event.instantaneous and event.starts_at < moment - lead:
            continue
        result.append(event)
    return result


# ---------------------------------------------------------------------------
# Общие помощники: окно прогноза, эпизоды, значения
# ---------------------------------------------------------------------------


def _window_hours(report: Report, start: datetime, end: datetime) -> list[AggregatedHour]:
    """Часы прогноза в окне [start, end), отсортированные по времени."""
    return [hour for hour in report.hours if start <= hour.time < end]


def _episodes(
    hours: list,
    predicate,
    max_gap: timedelta = EPISODE_GAP,
) -> list[list]:
    """Группирует подряд идущие часы, подходящие под условие."""
    episodes: list[list] = []
    current: list = []
    for hour in hours:
        if not predicate(hour):
            if current:
                episodes.append(current)
                current = []
            continue
        if current and hour.time - current[-1].time > max_gap:
            episodes.append(current)
            current = []
        current.append(hour)
    if current:
        episodes.append(current)
    return episodes


def _span(items: list) -> tuple[datetime, datetime]:
    """Начало и конец эпизода (конец — начало последнего часа плюс час)."""
    return items[0].time, items[-1].time + timedelta(hours=1)


def _peak(items: list, attribute: str) -> float | None:
    """Максимум медианы по площадкам за эпизод."""
    values: list[float] = []
    for hour in items:
        consensus = getattr(hour, attribute, None)
        if consensus is not None and consensus.median is not None:
            values.append(float(consensus.median))
    return max(values) if values else None


def _lowest(items: list, attribute: str = "temperature") -> float | None:
    """Минимум медианы по площадкам за эпизод."""
    values: list[float] = []
    for hour in items:
        consensus = getattr(hour, attribute, None)
        if consensus is not None and consensus.median is not None:
            values.append(float(consensus.median))
    return min(values) if values else None


def _sources(items: list) -> tuple[str, ...]:
    """Площадки, участвовавшие в эпизоде (для ссылки в тексте)."""
    names: list[str] = []
    for hour in items:
        for name in getattr(hour, "sources", ()) or ():
            if name not in names:
                names.append(name)
    return tuple(names)


def _number(value: float | None, unit: str, digits: int = 1) -> str:
    if value is None:
        return "н/д"
    return f"{value:.{digits}f} {unit}"


# ---------------------------------------------------------------------------
# Осадки: дождь, снег, гроза, град
# ---------------------------------------------------------------------------


def _is_wet(hour: AggregatedHour, settings: Settings) -> bool:
    """Есть ли признаки осадков с учётом порогов из настроек."""
    amount = hour.precipitation.median
    if amount is not None and amount >= settings.notify_rain_mm_per_hour:
        return True
    chance = hour.precipitation_probability.median
    return chance is not None and chance >= settings.notify_rain_probability


def _is_snowy(hour: AggregatedHour, settings: Settings) -> bool:
    """Снег по коду или по типу осадков при отрицательной температуре."""
    if wmo_is_snow(hour.weather_code):
        return True
    return _is_wet(hour, settings) and hour.kind in SNOW_KINDS


def _precipitation_events(hours: list[AggregatedHour], settings: Settings) -> list[Event]:
    events: list[Event] = []

    for items in _episodes(hours, lambda hour: _is_snowy(hour, settings)):
        events.append(_snow_event(items))

    for items in _episodes(hours, lambda hour: wmo_is_thunder(hour.weather_code)):
        events.append(_thunder_event(items))

    for items in _episodes(hours, lambda hour: wmo_is_hail(hour.weather_code)):
        events.append(_hail_event(items))

    for items in _episodes(
        hours,
        lambda hour: _is_wet(hour, settings)
        and not _is_snowy(hour, settings)
        and not wmo_is_thunder(hour.weather_code),
    ):
        events.append(_rain_event(items))
    return events


def _precipitation_details(items: list[AggregatedHour]) -> str:
    """Общая часть описания: сколько осадков и с какой вероятностью."""
    amount = _peak(items, "precipitation")
    chance = _peak(items, "precipitation_probability")
    parts = [f"до {_number(amount, 'мм/ч', 1)}"]
    if chance is not None:
        parts.append(f"вероятность {chance:.0f}%")
    return ", ".join(parts)


def _rain_event(items: list[AggregatedHour]) -> Event:
    start, end = _span(items)
    amount = _peak(items, "precipitation")
    heavy = (amount or 0.0) >= HEAVY_RAIN_MM_PER_HOUR
    return Event(
        kind=KIND_RAIN,
        level=LEVEL_DANGER if heavy else LEVEL_WARNING,
        starts_at=start,
        ends_at=end,
        summary="Сильный дождь" if heavy else "Ожидается дождь",
        details=_precipitation_details(items),
        sources=_sources(items),
    )


def _snow_event(items: list[AggregatedHour]) -> Event:
    start, end = _span(items)
    heavy = any(wmo_is_heavy(hour.weather_code) for hour in items)
    return Event(
        kind=KIND_SNOW,
        level=LEVEL_DANGER if heavy else LEVEL_WARNING,
        starts_at=start,
        ends_at=end,
        summary="Сильный снег" if heavy else "Ожидается снег",
        details=_precipitation_details(items),
        sources=_sources(items),
    )


def _thunder_event(items: list[AggregatedHour]) -> Event:
    start, end = _span(items)
    gust = _peak(items, "wind_gust")
    cape = _peak(items, "cape")
    parts: list[str] = []
    if gust is not None:
        parts.append(f"порывы до {_number(gust, 'км/ч', 0)}")
    if cape is not None:
        parts.append(f"CAPE до {_number(cape, 'Дж/кг', 0)}")
    heavy = any(wmo_is_heavy(hour.weather_code) for hour in items)
    return Event(
        kind=KIND_THUNDER,
        level=LEVEL_DANGER if heavy else LEVEL_WARNING,
        starts_at=start,
        ends_at=end,
        summary="Гроза с градом" if heavy else "Ожидается гроза",
        details=", ".join(parts),
        sources=_sources(items),
    )


def _hail_event(items: list[AggregatedHour]) -> Event:
    start, end = _span(items)
    gust = _peak(items, "wind_gust")
    return Event(
        kind=KIND_HAIL,
        level=LEVEL_DANGER,
        starts_at=start,
        ends_at=end,
        summary="Возможен град",
        details=f"порывы до {_number(gust, 'км/ч', 0)}" if gust is not None else "",
        sources=_sources(items),
    )


# ---------------------------------------------------------------------------
# Ветер, неустойчивость, туман, температура
# ---------------------------------------------------------------------------


def _gust(hour: AggregatedHour) -> float:
    """Порыв ветра, а при его отсутствии — средняя скорость."""
    value = hour.wind_gust.median
    if value is not None:
        return float(value)
    return float(hour.wind_speed.median or 0.0)


def _wind_events(hours: list[AggregatedHour], settings: Settings) -> list[Event]:
    events: list[Event] = []

    for items in _episodes(
        hours, lambda hour: _gust(hour) >= settings.notify_hurricane_gust_kmh
    ):
        events.append(_gust_event(items, KIND_HURRICANE, LEVEL_DANGER, "Ураганный ветер"))

    for items in _episodes(
        hours,
        lambda hour: settings.notify_gust_kmh
        <= _gust(hour)
        < settings.notify_hurricane_gust_kmh,
    ):
        events.append(_gust_event(items, KIND_SQUALL, LEVEL_WARNING, "Сильный ветер"))

    events.extend(_tornado_events(hours, settings))
    return events


def _gust_event(items: list[AggregatedHour], kind: str, level: int, summary: str) -> Event:
    start, end = _span(items)
    gust = _peak(items, "wind_gust")
    return Event(
        kind=kind,
        level=level,
        starts_at=start,
        ends_at=end,
        summary=summary,
        details=f"порывы до {_number(gust, 'км/ч', 0)}",
        sources=_sources(items),
    )


def _tornado_events(hours: list[AggregatedHour], settings: Settings) -> list[Event]:
    """Смерчи не предсказываем: помечаем окна с грозой и очень высокой неустойчивостью."""
    events: list[Event] = []

    def unstable(hour: AggregatedHour) -> bool:
        return (
            wmo_is_thunder(hour.weather_code)
            and (hour.cape.median or 0.0) >= settings.notify_tornado_cape
            and _gust(hour) >= settings.notify_gust_kmh
        )

    for items in _episodes(hours, unstable):
        start, end = _span(items)
        cape = _peak(items, "cape")
        events.append(
            Event(
                kind=KIND_TORNADO,
                level=LEVEL_DANGER,
                starts_at=start,
                ends_at=end,
                summary="Высокий риск шквалов и смерчей",
                details=f"CAPE до {_number(cape, 'Дж/кг', 0)}",
                sources=_sources(items),
            )
        )
    return events


def _fog_events(hours: list[AggregatedHour], settings: Settings) -> list[Event]:
    events: list[Event] = []
    for items in _episodes(hours, lambda hour: wmo_is_fog(hour.weather_code)):
        events.append(_fog_event(items, explicit=True))

    for items in _episodes(
        hours,
        lambda hour: not wmo_is_fog(hour.weather_code)
        and (hour.humidity.median or 0.0) >= 97.0
        and _gust(hour) <= 5.0,
    ):
        events.append(_fog_event(items, explicit=False))
    return events


def _fog_event(items: list[AggregatedHour], explicit: bool) -> Event:
    start, end = _span(items)
    humidity = _peak(items, "humidity")
    return Event(
        kind=KIND_FOG,
        level=LEVEL_INFO if explicit else LEVEL_WARNING,
        starts_at=start,
        ends_at=end,
        summary="Ожидается туман" if explicit else "Вероятен туман",
        details="" if explicit else f"влажность до {_number(humidity, '%', 0)}, штиль",
        sources=_sources(items),
    )


def _temperature_events(hours: list[AggregatedHour], settings: Settings) -> list[Event]:
    events: list[Event] = []

    for items in _episodes(
        hours, lambda hour: (hour.temperature.median or -100.0) >= settings.notify_heat_c
    ):
        events.append(_heat_event(items, settings))

    for items in _episodes(hours, lambda hour: (hour.temperature.median or 100.0) <= 0.0):
        events.append(_frost_event(items))

    drop = _cold_snap_event(hours, settings)
    if drop is not None:
        events.append(drop)
    return events


def _heat_event(items: list[AggregatedHour], settings: Settings) -> Event:
    start, end = _span(items)
    peak = _peak(items, "temperature")
    return Event(
        kind=KIND_HEAT,
        level=(
            LEVEL_DANGER
            if (peak or 0.0) >= settings.notify_heat_c + 5.0
            else LEVEL_WARNING
        ),
        starts_at=start,
        ends_at=end,
        summary="Жара",
        details=f"до {_number(peak, '°C', 0)}",
        sources=_sources(items),
    )


def _frost_event(items: list[AggregatedHour]) -> Event:
    start, end = _span(items)
    low = _lowest(items)
    return Event(
        kind=KIND_FROST,
        level=LEVEL_DANGER if (low or 0.0) <= -5.0 else LEVEL_WARNING,
        starts_at=start,
        ends_at=end,
        summary="Заморозки",
        details=f"минимум {_number(low, '°C', 0)}",
        sources=_sources(items),
    )


def _cold_snap_event(hours: list[AggregatedHour], settings: Settings) -> Event | None:
    """Ищет момент, когда температура падает на notify_temp_drop_c от максимума."""
    best: tuple[int, int, float] | None = None
    for index, hour in enumerate(hours):
        start_value = hour.temperature.median
        if start_value is None:
            continue
        for later_index in range(index + 1, len(hours)):
            later = hours[later_index].temperature.median
            if later is None:
                continue
            if later > start_value:
                break
            drop = float(start_value) - float(later)
            if drop >= settings.notify_temp_drop_c and (best is None or drop > best[2]):
                best = (index, later_index, drop)
    if best is None:
        return None

    start_index, end_index, drop = best
    items = hours[start_index : end_index + 1]
    begin, _ = _span(items)
    coldest = _lowest(items)
    return Event(
        kind=KIND_COLD_SNAP,
        level=LEVEL_WARNING,
        starts_at=begin,
        ends_at=hours[end_index].time + timedelta(hours=1),
        summary="Резкое похолодание",
        details=f"на {drop:.0f} °C, минимум {_number(coldest, '°C', 0)}",
        sources=_sources(items),
    )


# ---------------------------------------------------------------------------
# Море и землетрясения
# ---------------------------------------------------------------------------


def _marine_events(
    report: Report, settings: Settings, start: datetime, end: datetime
) -> list[Event]:
    hours = [hour for hour in report.marine_hours if start <= hour.time < end]
    if not hours:
        return []

    events: list[Event] = []
    for items in _episodes(
        hours,
        lambda hour: settings.notify_wave_m
        <= (hour.wave_height.median or 0.0)
        < settings.notify_wave_severe_m,
        max_gap=MARINE_GAP,
    ):
        events.append(_marine_event(items, KIND_MARINE, LEVEL_WARNING, "Сильное волнение"))

    for items in _episodes(
        hours,
        lambda hour: (hour.wave_height.median or 0.0) >= settings.notify_wave_severe_m,
        max_gap=MARINE_GAP,
    ):
        events.append(_marine_event(items, KIND_MARINE_STORM, LEVEL_DANGER, "Шторм на море"))
    return events


def _marine_event(
    items: list[AggregatedMarineHour], kind: str, level: int, summary: str
) -> Event:
    start, end = _span(items)
    peak = _peak(items, "wave_height")
    period = _peak(items, "wave_period")
    details = f"волна до {_number(peak, 'м', 1)}"
    if period is not None:
        details = f"{details}, период до {period:.0f} с"
    return Event(
        kind=kind,
        level=level,
        starts_at=start,
        ends_at=end,
        summary=summary,
        details=details,
    )


def _quake_events(report: Report, settings: Settings, now: datetime) -> list[Event]:
    """Свежие толчки рядом с точкой: напоминание проверить официальные сводки."""
    events: list[Event] = []
    for quake in report.quakes:
        moment = as_local(quake.time, settings.timezone)
        if moment is None:
            continue
        age_hours = (now - moment).total_seconds() / 3600.0
        if age_hours > QUAKE_RECENT_HOURS or age_hours < 0:
            continue

        magnitude = float(quake.magnitude or 0.0)
        shallow = quake.depth_km is not None and quake.depth_km <= QUAKE_DEPTH_SHALLOW_KM
        tsunami_risk = magnitude >= settings.tsunami_magnitude and shallow

        parts = [f"M{magnitude:.1f}"]
        if quake.place:
            parts.append(quake.place)
        if quake.distance_km is not None:
            parts.append(f"{quake.distance_km:.0f} км от точки")
        if quake.depth_km is not None:
            parts.append(f"глубина {quake.depth_km:.0f} км")

        events.append(
            Event(
                kind=KIND_TSUNAMI if tsunami_risk else KIND_QUAKE,
                level=LEVEL_DANGER if magnitude >= 5.0 else LEVEL_WARNING,
                starts_at=moment,
                ends_at=moment,
                summary=(
                    "Землетрясение: возможен риск цунами"
                    if tsunami_risk
                    else "Землетрясение рядом"
                ),
                details=", ".join(parts),
                sources=(quake.source,) if quake.source else (),
            )
        )
    return events
