"""Реестр площадок: сбор данных со всех источников параллельно."""

from __future__ import annotations

import asyncio

import httpx

from ..config import Settings
from ..models import SourceResult
from .base import USER_AGENT, SourceError, WeatherSource
from .open_meteo import OpenMeteoSource
from .open_meteo_marine import OpenMeteoMarineSource
from .openweathermap import OpenWeatherMapSource
from .usgs import UsgsQuakesSource
from .wttr_in import WttrInSource

__all__ = [
    "OpenMeteoSource",
    "OpenMeteoMarineSource",
    "OpenWeatherMapSource",
    "UsgsQuakesSource",
    "WttrInSource",
    "build_sources",
    "collect_all",
]


def build_sources(settings: Settings) -> tuple[list[WeatherSource], list[str]]:
    """Создаёт список площадок и причины пропуска недоступных.

    Возвращает: (включённые площадки, описания пропущенных).
    """
    candidates: list[WeatherSource] = [
        OpenMeteoSource(settings),
        OpenWeatherMapSource(settings),
        WttrInSource(settings),
        OpenMeteoMarineSource(settings),
        UsgsQuakesSource(settings),
    ]
    enabled: list[WeatherSource] = []
    skipped: list[str] = []
    for source in candidates:
        if source.enabled:
            enabled.append(source)
        else:
            reason = source.disabled_reason or "недоступна"
            skipped.append(f"{source.name} пропущен: {reason}")
    return enabled, skipped


async def collect_all(
    settings: Settings, sources: list[WeatherSource] | None = None
) -> tuple[list[SourceResult], list[str]]:
    """Опрашивает все площадки параллельно, ошибки превращает в SourceResult.error."""
    if sources is None:
        sources, skipped = build_sources(settings)
    else:
        skipped = []

    if not sources:
        return [], skipped

    async with httpx.AsyncClient(
        timeout=settings.request_timeout, headers={"User-Agent": USER_AGENT}
    ) as client:
        outcomes = await asyncio.gather(
            *(source.collect(client) for source in sources), return_exceptions=True
        )

    results: list[SourceResult] = []
    for source, outcome in zip(sources, outcomes):
        if isinstance(outcome, SourceResult):
            results.append(outcome)
        elif isinstance(outcome, SourceError):
            results.append(SourceResult(source=source.name, error=str(outcome)))
        elif isinstance(outcome, BaseException):
            results.append(
                SourceResult(source=source.name, error=f"{outcome.__class__.__name__}: {outcome}")
            )
        else:  # pragma: no cover - защита от неожиданного типа
            results.append(SourceResult(source=source.name, error="неизвестный ответ"))
    return results, skipped
