"""Площадка Open-Meteo: мультимодельная сводка (ICON/DWD, GFS, ECMWF), без ключа."""

from __future__ import annotations

import httpx

from ..codes import normalize_wmo
from ..config import Settings
from ..models import CurrentWeather, DayForecast, HourlyPoint, SourceResult
from ..wmo import wmo_description
from .base import (
    SourceError,
    WeatherSource,
    parse_date,
    parse_local_dt,
    pick,
    time_only,
    to_float,
)

API_URL = "https://api.open-meteo.com/v1/forecast"
NAME = "Open-Meteo"

CURRENT_FIELDS = (
    "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,"
    "weather_code,cloud_cover,pressure_msl,wind_speed_10m,wind_direction_10m,wind_gusts_10m"
)
DAILY_FIELDS = (
    "weather_code,temperature_2m_max,temperature_2m_min,apparent_temperature_max,"
    "sunrise,sunset,precipitation_sum,precipitation_probability_max,"
    "wind_speed_10m_max,wind_gusts_10m_max,uv_index_max"
)
# CAPE нужен детектору событий: высокая энергия неустойчивости = риск грозы/шквала.
HOURLY_FIELDS = (
    "temperature_2m,apparent_temperature,precipitation,precipitation_probability,"
    "weather_code,wind_speed_10m,wind_gusts_10m,wind_direction_10m,cloud_cover,"
    "relative_humidity_2m,cape"
)
MAX_HOURS = 48


class OpenMeteoSource(WeatherSource):
    """Текущая погода, прогноз по суткам и время рассвета/заката."""

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
        """Параметры запроса к /v1/forecast для точки прогноза."""
        return {
            "latitude": settings.lat,
            "longitude": settings.lon,
            "current": CURRENT_FIELDS,
            "daily": DAILY_FIELDS,
            "hourly": HOURLY_FIELDS,
            "timezone": settings.timezone,
            "forecast_days": settings.forecast_days,
            "wind_speed_unit": "kmh",
        }


def parse(payload: dict, settings: Settings) -> SourceResult:
    """Нормализует ответ /v1/forecast в SourceResult."""
    current_payload = payload.get("current") or {}
    current = CurrentWeather(
        source=NAME,
        observed_at=parse_local_dt(current_payload.get("time"), settings.timezone),
        temperature=to_float(current_payload.get("temperature_2m")),
        feels_like=to_float(current_payload.get("apparent_temperature")),
        humidity=to_float(current_payload.get("relative_humidity_2m")),
        pressure=to_float(current_payload.get("pressure_msl")),
        wind_speed=to_float(current_payload.get("wind_speed_10m")),
        wind_gust=to_float(current_payload.get("wind_gusts_10m")),
        wind_direction=to_float(current_payload.get("wind_direction_10m")),
        cloud_cover=to_float(current_payload.get("cloud_cover")),
        precipitation=to_float(current_payload.get("precipitation")),
        description=wmo_description(current_payload.get("weather_code")),
    )

    daily = payload.get("daily") or {}
    dates = daily.get("time") or []
    days: list[DayForecast] = []
    for index, raw_date in enumerate(dates):
        day = parse_date(raw_date)
        if day is None:
            continue
        days.append(
            DayForecast(
                source=NAME,
                day=day,
                temp_min=to_float(pick(daily.get("temperature_2m_min"), index)),
                temp_max=to_float(pick(daily.get("temperature_2m_max"), index)),
                feels_like_max=to_float(pick(daily.get("apparent_temperature_max"), index)),
                precipitation_sum=to_float(pick(daily.get("precipitation_sum"), index)),
                precipitation_probability=to_float(
                    pick(daily.get("precipitation_probability_max"), index)
                ),
                wind_speed_max=to_float(pick(daily.get("wind_speed_10m_max"), index)),
                wind_gust_max=to_float(pick(daily.get("wind_gusts_10m_max"), index)),
                uv_index_max=to_float(pick(daily.get("uv_index_max"), index)),
                description=wmo_description(pick(daily.get("weather_code"), index)),
                sunrise=time_only(pick(daily.get("sunrise"), index)),
                sunset=time_only(pick(daily.get("sunset"), index)),
            )
        )

    return SourceResult(
        source=NAME, current=current, days=days, hourly=_parse_hourly(payload, settings)
    )


def _parse_hourly(payload: dict, settings: Settings) -> list[HourlyPoint]:
    """Разбирает блок hourly: до MAX_HOURS часов вперёд для детектора событий."""
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    points: list[HourlyPoint] = []
    for index, raw_time in enumerate(times[:MAX_HOURS]):
        moment = parse_local_dt(raw_time, settings.timezone)
        if moment is None:
            continue
        code = pick(hourly.get("weather_code"), index)
        points.append(
            HourlyPoint(
                time=moment,
                source=NAME,
                temperature=to_float(pick(hourly.get("temperature_2m"), index)),
                feels_like=to_float(pick(hourly.get("apparent_temperature"), index)),
                precipitation=to_float(pick(hourly.get("precipitation"), index)),
                precipitation_probability=to_float(
                    pick(hourly.get("precipitation_probability"), index)
                ),
                wind_speed=to_float(pick(hourly.get("wind_speed_10m"), index)),
                wind_gust=to_float(pick(hourly.get("wind_gusts_10m"), index)),
                wind_direction=to_float(pick(hourly.get("wind_direction_10m"), index)),
                cloud_cover=to_float(pick(hourly.get("cloud_cover"), index)),
                humidity=to_float(pick(hourly.get("relative_humidity_2m"), index)),
                cape=to_float(pick(hourly.get("cape"), index)),
                weather_code=normalize_wmo(code),
                description=wmo_description(code),
            )
        )
    return points
