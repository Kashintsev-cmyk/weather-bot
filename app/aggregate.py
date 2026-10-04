"""Обобщение данных площадок: медиана по полям, разброс и мода описаний."""

from __future__ import annotations

import math
from collections import Counter
from datetime import date, datetime
from statistics import median

from .config import Settings
from .models import (
    AggregatedCurrent,
    AggregatedDay,
    AggregatedHour,
    AggregatedMarine,
    AggregatedMarineDay,
    AggregatedMarineHour,
    Consensus,
    CurrentWeather,
    DayForecast,
    HourlyPoint,
    MarineCurrent,
    MarineDay,
    MarineHour,
    Quake,
    Report,
    SourceResult,
)
from .timeutil import as_local, local_now

CURRENT_FIELDS = (
    "temperature",
    "feels_like",
    "humidity",
    "pressure",
    "wind_speed",
    "wind_gust",
    "cloud_cover",
    "precipitation",
)
DAY_FIELDS = (
    "temp_min",
    "temp_max",
    "feels_like_max",
    "precipitation_sum",
    "precipitation_probability",
    "wind_speed_max",
    "wind_gust_max",
    "uv_index_max",
)
MARINE_FIELDS = ("sea_temperature", "wave_height", "wave_period", "swell_height")
MARINE_DAY_FIELDS = (
    "wave_height_max",
    "wave_period_max",
    "sea_temp_min",
    "sea_temp_max",
)
HOUR_FIELDS = (
    "temperature",
    "precipitation",
    "precipitation_probability",
    "wind_speed",
    "wind_gust",
    "cloud_cover",
    "humidity",
    "cape",
)
MARINE_HOUR_FIELDS = ("wave_height", "wave_period", "sea_temperature")

# С какого расхождения температур по площадкам добавляем предупреждение в сводку.
TEMP_SPREAD_WARNING = 3.0


def build_report(
    settings: Settings,
    results: list[SourceResult],
    skipped: list[str] | None = None,
    now: datetime | None = None,
) -> Report:
    """Собирает обобщённую сводку по результатам всех площадок."""
    report = Report(
        city=settings.city,
        lat=settings.lat,
        lon=settings.lon,
        generated_at=now or local_now(settings.timezone),
        timezone_label=settings.timezone_label,
        sources_ok=[result.source for result in results if result.ok and result.has_data],
        sources_failed=[
            (result.source, result.error or "неизвестная ошибка")
            for result in results
            if not result.ok
        ],
        sources_skipped=list(skipped or []),
        raw_results=list(results),
    )

    usable = [result for result in results if result.ok and result.has_data]

    currents = [result.current for result in usable if result.current is not None]
    if currents:
        report.current = _aggregate_current(currents, settings)

    report.days = _aggregate_days(usable, settings)
    report.hours = _aggregate_hours(usable)

    marine_currents = [
        result.marine_current for result in usable if result.marine_current is not None
    ]
    if marine_currents:
        report.marine = _aggregate_marine(marine_currents, settings)

    report.marine_days = _aggregate_marine_days(usable, settings)
    report.marine_hours = _aggregate_marine_hours(usable)
    report.quakes = _collect_quakes(usable)
    report.notes = _build_notes(report)
    return report


# ---------------------------------------------------------------------------
# Консенсус значений
# ---------------------------------------------------------------------------


def consensus(values: dict[str, float]) -> Consensus:
    """Медиана, минимум и максимум по значениям площадок."""
    clean = {source: float(value) for source, value in values.items() if value is not None}
    if not clean:
        return Consensus()
    numbers = sorted(clean.values())
    return Consensus(
        median=median(numbers), low=numbers[0], high=numbers[-1], by_source=clean
    )


def circular_mean(degrees: list[float]) -> float | None:
    """Среднее направление (градусы) с учётом перехода через 360°."""
    if not degrees:
        return None
    radians = [math.radians(value) for value in degrees]
    sin_sum = sum(math.sin(value) for value in radians)
    cos_sum = sum(math.cos(value) for value in radians)
    if abs(sin_sum) < 1e-9 and abs(cos_sum) < 1e-9:
        return None
    return round(math.degrees(math.atan2(sin_sum, cos_sum)) % 360, 1)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _values_by_source(items: list[tuple[str, object]], attribute: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for source, sample in items:
        number = _number(getattr(sample, attribute, None))
        if number is not None:
            values[source] = number
    return values


def _consensus_by_source(items: list[tuple[str, object]], attribute: str) -> Consensus:
    return consensus(_values_by_source(items, attribute))


def _direction_consensus(items: list[tuple[str, object]], attribute: str) -> Consensus:
    """Для направлений берём круговое среднее вместо обычной медианы."""
    values = _values_by_source(items, attribute)
    result = consensus(values)
    mean = circular_mean(list(values.values()))
    if mean is not None:
        result.median = mean
    return result


def _dominant(descriptions: dict[str, str]) -> str | None:
    """Самое частое описание погоды среди площадок."""
    normalized = [
        " ".join(text.lower().split()) for text in descriptions.values() if text.strip()
    ]
    if not normalized:
        return None
    return Counter(normalized).most_common(1)[0][0]


def _most_common_value(values: list[str | None]) -> str | None:
    clean = [value for value in values if value]
    if not clean:
        return None
    return Counter(clean).most_common(1)[0][0]


# ---------------------------------------------------------------------------
# Обобщение текущих условий, прогнозов и моря
# ---------------------------------------------------------------------------


def _aggregate_current(
    currents: list[CurrentWeather], settings: Settings
) -> AggregatedCurrent:
    items = [(current.source, current) for current in currents]
    descriptions = {
        current.source: current.description
        for current in currents
        if current.description
    }
    moments = [current.observed_at for current in currents if current.observed_at]

    aggregated = AggregatedCurrent(
        observed_at=as_local(max(moments), settings.timezone) if moments else None,
        description=_dominant(descriptions),
        descriptions=descriptions,
    )
    for field in CURRENT_FIELDS:
        setattr(aggregated, field, _consensus_by_source(items, field))
    aggregated.wind_direction = _direction_consensus(items, "wind_direction")
    return aggregated


def _aggregate_days(results: list[SourceResult], settings: Settings) -> list[AggregatedDay]:
    buckets: dict[date, list[tuple[str, DayForecast]]] = {}
    for result in results:
        for forecast in result.days:
            buckets.setdefault(forecast.day, []).append((result.source, forecast))

    days: list[AggregatedDay] = []
    for day in sorted(buckets)[: settings.forecast_days]:
        items = buckets[day]
        descriptions = {
            source: forecast.description
            for source, forecast in items
            if forecast.description
        }
        aggregated = AggregatedDay(
            day=day,
            description=_dominant(descriptions),
            descriptions=descriptions,
            sunrise=_most_common_value([forecast.sunrise for _, forecast in items]),
            sunset=_most_common_value([forecast.sunset for _, forecast in items]),
        )
        for field in DAY_FIELDS:
            setattr(aggregated, field, _consensus_by_source(items, field))
        days.append(aggregated)
    return days


def _aggregate_marine(
    marine_currents: list[MarineCurrent], settings: Settings
) -> AggregatedMarine:
    items = [(marine.source, marine) for marine in marine_currents]
    moments = [marine.observed_at for marine in marine_currents if marine.observed_at]

    aggregated = AggregatedMarine(
        observed_at=as_local(max(moments), settings.timezone) if moments else None
    )
    for field in MARINE_FIELDS:
        setattr(aggregated, field, _consensus_by_source(items, field))
    aggregated.wave_direction = _direction_consensus(items, "wave_direction")
    return aggregated


def _aggregate_marine_days(
    results: list[SourceResult], settings: Settings
) -> list[AggregatedMarineDay]:
    buckets: dict[date, list[tuple[str, MarineDay]]] = {}
    for result in results:
        for forecast in result.marine_days:
            buckets.setdefault(forecast.day, []).append((result.source, forecast))

    days: list[AggregatedMarineDay] = []
    for day in sorted(buckets)[: settings.forecast_days]:
        items = buckets[day]
        aggregated = AggregatedMarineDay(day=day)
        for field in MARINE_DAY_FIELDS:
            setattr(aggregated, field, _consensus_by_source(items, field))
        aggregated.wave_direction_dominant = _direction_consensus(
            items, "wave_direction_dominant"
        )
        days.append(aggregated)
    return days


def _aggregate_hours(results: list[SourceResult]) -> list[AggregatedHour]:
    """Собирает почасовой консенсус: площадки приводятся к одному часу.

    open-meteo отдаёт шаг 1 час, OpenWeatherMap и wttr.in — 3 часа;
    осадки у трёхчасовых записей приводятся к мм/ч, поэтому медианы сопоставимы.
    """
    buckets: dict[datetime, list[tuple[str, HourlyPoint]]] = {}
    for result in results:
        for point in result.hourly:
            buckets.setdefault(_hour_start(point.time), []).append((result.source, point))

    hours: list[AggregatedHour] = []
    for moment in sorted(buckets):
        items = buckets[moment]
        descriptions = {
            source: point.description for source, point in items if point.description
        }
        codes = [point.weather_code for _, point in items if point.weather_code is not None]
        aggregated = AggregatedHour(
            time=moment,
            weather_code=Counter(codes).most_common(1)[0][0] if codes else None,
            description=_dominant(descriptions),
            descriptions=descriptions,
            sources=tuple(source for source, _ in items),
        )
        for field in HOUR_FIELDS:
            setattr(aggregated, field, _hour_consensus(items, field))
        hours.append(aggregated)
    return hours


def _aggregate_marine_hours(results: list[SourceResult]) -> list[AggregatedMarineHour]:
    """Собирает почасовой консенсус по волнению и температуре воды."""
    buckets: dict[datetime, list[tuple[str, MarineHour]]] = {}
    for result in results:
        for point in result.marine_hours:
            buckets.setdefault(_hour_start(point.time), []).append((result.source, point))

    hours: list[AggregatedMarineHour] = []
    for moment in sorted(buckets):
        items = buckets[moment]
        aggregated = AggregatedMarineHour(time=moment)
        for field in MARINE_HOUR_FIELDS:
            setattr(aggregated, field, _consensus_by_source(items, field))
        hours.append(aggregated)
    return hours


def _collect_quakes(results: list[SourceResult]) -> list[Quake]:
    """Землетрясения из всех площадок: без дублей, свежие сверху."""
    seen: set[tuple[str, float | None]] = set()
    quakes: list[Quake] = []
    for result in results:
        for quake in result.quakes:
            key = (f"{quake.time.isoformat()}|{quake.place}", quake.magnitude)
            if key in seen:
                continue
            seen.add(key)
            quakes.append(quake)
    quakes.sort(key=lambda item: item.time, reverse=True)
    return quakes


def _hour_start(moment: datetime) -> datetime:
    """Начало часа, к которому относится момент."""
    return moment.replace(minute=0, second=0, microsecond=0)


def _hour_consensus(
    items: list[tuple[str, object]], attribute: str
) -> Consensus:
    """Консенсус по часам; для осадков берём мм/ч, а не сумму за интервал."""
    values: dict[str, float] = {}
    for source, point in items:
        if attribute == "precipitation":
            number = _number(getattr(point, "precipitation_per_hour", None))
        else:
            number = _number(getattr(point, attribute, None))
        if number is not None:
            values[source] = round(number, 2)
    return consensus(values)


def _build_notes(report: Report) -> list[str]:
    """Заметки о сбоях площадок и расхождениях между ними."""
    notes: list[str] = []
    notes.extend(report.sources_skipped)
    for source, error in report.sources_failed:
        notes.append(f"{source}: данные не получены ({error})")

    current = report.current
    if current is not None:
        spread = current.temperature.spread
        if spread is not None and spread >= TEMP_SPREAD_WARNING:
            notes.append(
                f"Расхождение температур между площадками: {spread:.0f} °C "
                f"({current.temperature.low:+.0f}…{current.temperature.high:+.0f} °C) — "
                "рекомендуем ориентироваться на медиану."
            )
    if not report.sources_ok:
        notes.append("Ни одна площадка не отдала данные — проверьте ключи и связь.")
    return notes

