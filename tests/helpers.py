"""Помощники для тестов: чтение сохранённых ответов площадок и сборка сводок."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from app.config import Settings
from app.models import (
    AggregatedHour,
    AggregatedMarineHour,
    Consensus,
    Quake,
    Report,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> dict:
    """Читает JSON-фикстуру ответа площадки из tests/fixtures."""
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _consensus(value: float | None) -> Consensus:
    """Консенсус с одним значением: медиана, минимум и максимум совпадают."""
    if value is None:
        return Consensus()
    return Consensus(median=value, low=value, high=value)


def hour(
    moment: datetime,
    temperature: float | None = 20.0,
    precipitation: float | None = None,
    probability: float | None = None,
    wind_speed: float | None = None,
    wind_gust: float | None = None,
    humidity: float | None = None,
    cloud_cover: float | None = None,
    cape: float | None = None,
    weather_code: int | None = None,
    sources: tuple[str, ...] = ("Open-Meteo",),
) -> AggregatedHour:
    """Обобщённый час: все площадки согласны, значения задаются напрямую."""
    return AggregatedHour(
        time=moment,
        temperature=_consensus(temperature),
        precipitation=_consensus(precipitation),
        precipitation_probability=_consensus(probability),
        wind_speed=_consensus(wind_speed),
        wind_gust=_consensus(wind_gust),
        humidity=_consensus(humidity),
        cloud_cover=_consensus(cloud_cover),
        cape=_consensus(cape),
        weather_code=weather_code,
        sources=sources,
    )


def hours(now: datetime, specs: list[dict]) -> list[AggregatedHour]:
    """Строит часы по списку словарей с обязательным ключом shift (часы от now)."""
    built: list[AggregatedHour] = []
    for spec in specs:
        params = dict(spec)
        shift = params.pop("shift")
        built.append(hour(now + timedelta(hours=shift), **params))
    return built


def marine_hour(
    moment: datetime,
    wave_height: float | None = None,
    wave_period: float | None = None,
    sea_temperature: float | None = None,
) -> AggregatedMarineHour:
    """Обобщённый час моря."""
    return AggregatedMarineHour(
        time=moment,
        wave_height=_consensus(wave_height),
        wave_period=_consensus(wave_period),
        sea_temperature=_consensus(sea_temperature),
    )


def marine_hours(now: datetime, specs: list[dict]) -> list[AggregatedMarineHour]:
    """Строит морские часы по списку словарей с ключом shift."""
    built: list[AggregatedMarineHour] = []
    for spec in specs:
        params = dict(spec)
        shift = params.pop("shift")
        built.append(marine_hour(now + timedelta(hours=shift), **params))
    return built


def quake(
    moment: datetime,
    magnitude: float,
    depth_km: float | None = 10.0,
    distance_km: float | None = 100.0,
    place: str = "рядом с Адлером",
) -> Quake:
    """Землетрясение из ленты USGS."""
    return Quake(
        source="USGS",
        time=moment,
        magnitude=magnitude,
        depth_km=depth_km,
        distance_km=distance_km,
        place=place,
    )


def report(
    settings: Settings,
    built_hours: list[AggregatedHour] | None = None,
    built_marine_hours: list[AggregatedMarineHour] | None = None,
    quakes: list[Quake] | None = None,
    now: datetime | None = None,
) -> Report:
    """Готовая сводка без обращения к площадкам (для детектора и рассылки)."""
    return Report(
        city=settings.city,
        lat=settings.lat,
        lon=settings.lon,
        generated_at=now or datetime.now(tz=settings_timezone(settings)),
        timezone_label=settings.timezone_label,
        sources_ok=["Open-Meteo"],
        hours=built_hours or [],
        marine_hours=built_marine_hours or [],
        quakes=quakes or [],
    )


def settings_timezone(settings: Settings):
    """Часовой пояс настроек (для построения моментов времени в тестах)."""
    from app.timeutil import safe_zone

    return safe_zone(settings.timezone)

