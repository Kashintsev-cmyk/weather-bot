"""Площадка Open-Meteo: мультимодельная сводка (ICON/DWD, GFS, ECMWF), без ключа."""

from __future__ import annotations

from collections import Counter
from datetime import date

import httpx

from ..codes import normalize_wmo
from ..config import Settings
from ..models import CurrentWeather, DayForecast, HourlyPoint, SourceResult
from ..timeutil import local_now
from ..wmo import wmo_description
from .base import (
    ParamValue,
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
    def build_params(settings: Settings) -> dict[str, ParamValue]:
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

    # Мода почасовых кодов за сутки — вместо самого опасного daily.weather_code.
    # daily.weather_code отвечает на вопрос "что худшее случилось за день",
    # и один час дождя даёт "ливень весь день". Для описания режима дня
    # берём самый частый почасовой код.
    hourly_block = payload.get("hourly") or {}
    hourly_times = hourly_block.get("time") or []
    hourly_codes = hourly_block.get("weather_code") or []
    code_buckets: dict[date, list[int]] = {}
    for i, raw_time in enumerate(hourly_times):
        moment = parse_local_dt(raw_time, settings.timezone)
        code = pick(hourly_codes, i)
        if moment is None or code is None:
            continue
        try:
            code_buckets.setdefault(moment.date(), []).append(int(code))
        except (TypeError, ValueError):
            continue
    daily_modes: dict[date, int] = {
        d: Counter(codes).most_common(1)[0][0]
        for d, codes in code_buckets.items()
        if codes
    }

    days: list[DayForecast] = []
    for index, raw_date in enumerate(dates):
        day = parse_date(raw_date)
        if day is None:
            continue
        mode_code = daily_modes.get(day)
        if mode_code is None:
            mode_code = pick(daily.get("weather_code"), index)
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
                description=wmo_description(mode_code),
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

    # Окно от текущего часа, а не от 00:00 суток: иначе к вечеру
    # MAX_HOURS точек покрывают заметно меньше суток вперёд.
    now_hour = local_now(settings.timezone).replace(
        minute=0, second=0, microsecond=0
    )
    start_index = 0
    for i, raw in enumerate(times):
        moment = parse_local_dt(raw, settings.timezone)
        if moment is not None and moment >= now_hour:
            start_index = i
            break

    points: list[HourlyPoint] = []
    for index in range(start_index, min(start_index + MAX_HOURS, len(times))):
        raw_time = times[index]
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
