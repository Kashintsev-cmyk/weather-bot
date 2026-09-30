"""Площадка Open-Meteo Marine API: температура воды, волнение, зыбь."""

from __future__ import annotations

import httpx

from ..config import Settings
from ..models import MarineCurrent, MarineDay, MarineHour, SourceResult
from .base import (
    SourceError,
    WeatherSource,
    parse_date,
    parse_local_dt,
    pick,
    to_float,
)

API_URL = "https://marine-api.open-meteo.com/v1/marine"
NAME = "Open-Meteo Marine"

CURRENT_FIELDS = (
    "wave_height,wave_direction,wave_period,swell_wave_height,swell_wave_direction,"
    "sea_surface_temperature"
)
DAILY_FIELDS = (
    "wave_height_max,wave_period_max,wave_direction_dominant,"
    "sea_surface_temperature_max,sea_surface_temperature_min"
)
HOURLY_FIELDS = "wave_height,wave_period,wave_direction,sea_surface_temperature"
MAX_HOURS = 48


class OpenMeteoMarineSource(WeatherSource):
    """Состояние Чёрного моря у точки прогноза и прогноз волнения."""

    name = NAME

    async def collect(self, client: httpx.AsyncClient) -> SourceResult:
        payload = await self._get_json(
            client, API_URL, params=self.build_params(self.settings)
        )
        try:
            return parse(payload, self.settings)
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceError(f"неожиданный формат ответа: {exc!r}") from exc

    @staticmethod
    def build_params(settings: Settings) -> dict[str, object]:
        """Параметры запроса к Marine API для точки прогноза."""
        return {
            "latitude": settings.lat,
            "longitude": settings.lon,
            "current": CURRENT_FIELDS,
            "daily": DAILY_FIELDS,
            "hourly": HOURLY_FIELDS,
            "timezone": settings.timezone,
            "forecast_days": settings.forecast_days,
        }


def parse(payload: dict, settings: Settings) -> SourceResult:
    """Нормализует ответ Marine API в SourceResult (только морские поля)."""
    current_payload = payload.get("current") or {}
    marine_current = MarineCurrent(
        source=NAME,
        observed_at=parse_local_dt(current_payload.get("time"), settings.timezone),
        sea_temperature=to_float(current_payload.get("sea_surface_temperature")),
        wave_height=to_float(current_payload.get("wave_height")),
        wave_period=to_float(current_payload.get("wave_period")),
        wave_direction=to_float(current_payload.get("wave_direction")),
        swell_height=to_float(current_payload.get("swell_wave_height")),
    )

    daily = payload.get("daily") or {}
    dates = daily.get("time") or []
    marine_days: list[MarineDay] = []
    for index, raw_date in enumerate(dates):
        day = parse_date(raw_date)
        if day is None:
            continue
        marine_days.append(
            MarineDay(
                source=NAME,
                day=day,
                wave_height_max=to_float(pick(daily.get("wave_height_max"), index)),
                wave_period_max=to_float(pick(daily.get("wave_period_max"), index)),
                wave_direction_dominant=to_float(
                    pick(daily.get("wave_direction_dominant"), index)
                ),
                sea_temp_min=to_float(
                    pick(daily.get("sea_surface_temperature_min"), index)
                ),
                sea_temp_max=to_float(
                    pick(daily.get("sea_surface_temperature_max"), index)
                ),
            )
        )

    return SourceResult(
        source=NAME,
        marine_current=marine_current,
        marine_days=marine_days,
        marine_hours=_parse_hourly(payload, settings),
    )


def _parse_hourly(payload: dict, settings: Settings) -> list[MarineHour]:
    """Разбирает почасовое волнение: до MAX_HOURS часов для алертов о шторме."""
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    hours: list[MarineHour] = []
    for index, raw_time in enumerate(times[:MAX_HOURS]):
        moment = parse_local_dt(raw_time, settings.timezone)
        if moment is None:
            continue
        hours.append(
            MarineHour(
                time=moment,
                source=NAME,
                sea_temperature=to_float(pick(hourly.get("sea_surface_temperature"), index)),
                wave_height=to_float(pick(hourly.get("wave_height"), index)),
                wave_period=to_float(pick(hourly.get("wave_period"), index)),
                wave_direction=to_float(pick(hourly.get("wave_direction"), index)),
            )
        )
    return hours
