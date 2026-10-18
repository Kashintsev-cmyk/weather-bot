"""Площадка OpenWeatherMap: текущая погода и прогноз трёхчасовыми шагами."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, timedelta, timezone

import httpx

from ..codes import wmo_from_owm
from ..config import Settings
from ..models import CurrentWeather, DayForecast, HourlyPoint, SourceResult
from ..timeutil import safe_zone
from .base import ParamValue, SourceError, WeatherSource, to_float

CURRENT_URL = "https://api.openweathermap.org/data/2.5/weather"
FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"
NAME = "OpenWeatherMap"
MS_TO_KMH = 3.6


class OpenWeatherMapSource(WeatherSource):
    """Работает только при наличии API-ключа в OPENWEATHER_API_KEY."""

    name = NAME
    requires_key = True

    @property
    def enabled(self) -> bool:
        return self.settings.has_openweather

    @property
    def disabled_reason(self) -> str | None:
        if self.settings.has_openweather:
            return None
        return "не задан ключ OPENWEATHER_API_KEY"

    async def collect(self, client: httpx.AsyncClient) -> SourceResult:
        params = self.build_params(self.settings)
        current_payload = await self._get_json(client, CURRENT_URL, params=params)

        # Прогноз 3ч/5 дней — необязательная часть: без него сводка всё равно полезна.
        forecast_payload: dict = {}
        try:
            forecast_payload = await self._get_json(client, FORECAST_URL, params=params)
        except SourceError:
            forecast_payload = {}

        try:
            return parse(current_payload, forecast_payload, self.settings)
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceError(f"неожиданный формат ответа: {exc!r}") from exc

    @staticmethod
    def build_params(settings: Settings) -> dict[str, ParamValue]:
        """Параметры запроса: метрическая система и русские описания."""
        return {
            "lat": settings.lat,
            "lon": settings.lon,
            "appid": settings.openweather_api_key,
            "units": "metric",
            "lang": "ru",
        }


def parse(
    current_payload: dict, forecast_payload: dict, settings: Settings
) -> SourceResult:
    """Нормализует ответы /weather и /forecast в SourceResult."""
    current = _parse_current(current_payload)
    days = _parse_daily(forecast_payload, settings)

    today = _local_date(current_payload)
    if today is not None:
        system = current_payload.get("sys") or {}
        shift = current_payload.get("timezone")
        for day in days:
            if day.day == today:
                day.sunrise = _clock(system.get("sunrise"), shift)
                day.sunset = _clock(system.get("sunset"), shift)

    return SourceResult(
        source=NAME,
        current=current,
        days=days,
        hourly=_parse_hourly(forecast_payload, settings),
    )


def _parse_hourly(forecast_payload: dict, settings: Settings) -> list[HourlyPoint]:
    """Разбирает трёхчасовые записи /forecast в почасовые точки (span_hours=3)."""
    entries = forecast_payload.get("list") or []
    if not isinstance(entries, list):
        return []

    points: list[HourlyPoint] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        moment = _entry_moment(entry, settings)
        if moment is None:
            continue
        main = entry.get("main") or {}
        wind = entry.get("wind") or {}
        clouds = entry.get("clouds") or {}
        probability = to_float(entry.get("pop"))
        points.append(
            HourlyPoint(
                time=moment,
                source=NAME,
                span_hours=3.0,
                temperature=to_float(main.get("temp")),
                feels_like=to_float(main.get("feels_like")),
                precipitation=_precipitation(entry),
                precipitation_probability=(
                    None if probability is None else round(probability * 100)
                ),
                wind_speed=_kmh(wind.get("speed")),
                wind_gust=_kmh(wind.get("gust")),
                wind_direction=to_float(wind.get("deg")),
                cloud_cover=to_float(clouds.get("all")),
                humidity=to_float(main.get("humidity")),
                weather_code=_entry_code(entry),
                description=_entry_description(entry),
            )
        )
    return points


def _entry_moment(entry: dict, settings: Settings) -> datetime | None:
    """Момент записи прогноза, приведённый к часовому поясу точки."""
    epoch = to_float(entry.get("dt"))
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=UTC).astimezone(
        safe_zone(settings.timezone)
    )


def _entry_code(entry: dict) -> int | None:
    """Нормализованный код WMO из id условия OWM."""
    weather = entry.get("weather")
    if isinstance(weather, list) and weather:
        return wmo_from_owm((weather[0] or {}).get("id"))
    return None


def _parse_current(payload: dict) -> CurrentWeather:
    main = payload.get("main") or {}
    wind = payload.get("wind") or {}
    clouds = payload.get("clouds") or {}
    return CurrentWeather(
        source=NAME,
        observed_at=_observed_at(payload.get("dt"), payload.get("timezone")),
        temperature=to_float(main.get("temp")),
        feels_like=to_float(main.get("feels_like")),
        humidity=to_float(main.get("humidity")),
        pressure=to_float(main.get("pressure")),
        wind_speed=_kmh(wind.get("speed")),
        wind_gust=_kmh(wind.get("gust")),
        wind_direction=to_float(wind.get("deg")),
        cloud_cover=to_float(clouds.get("all")),
        precipitation=_precipitation(payload),
        description=_entry_description(payload),
    )


def _parse_daily(forecast_payload: dict, settings: Settings) -> list[DayForecast]:
    entries = forecast_payload.get("list") or []
    if not isinstance(entries, list):
        return []

    city = forecast_payload.get("city") or {}
    shift = to_float(city.get("timezone")) or 0.0

    buckets: dict[date, list[dict]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        moment = _entry_local_moment(entry, shift)
        if moment is None:
            continue
        buckets.setdefault(moment.date(), []).append(entry)

    days: list[DayForecast] = []
    for day in sorted(buckets)[: settings.forecast_days]:
        days.append(_day_forecast(day, buckets[day]))
    return days


def _day_forecast(day: date, entries: list[dict]) -> DayForecast:
    temperatures = _numbers(entries, ("main", "temp"))
    feels = _numbers(entries, ("main", "feels_like"))
    winds = _positive([_kmh(value) for value in _numbers(entries, ("wind", "speed"))])
    gusts = _positive([_kmh(value) for value in _numbers(entries, ("wind", "gust"))])
    probabilities = _numbers(entries, ("pop",))

    precipitation = _sum_3h(entries, "rain") + _sum_3h(entries, "snow")
    descriptions = [
        text for text in (_entry_description(entry) for entry in entries) if text
    ]
    top_description = Counter(descriptions).most_common(1)[0][0] if descriptions else None

    return DayForecast(
        source=NAME,
        day=day,
        temp_min=min(temperatures) if temperatures else None,
        temp_max=max(temperatures) if temperatures else None,
        feels_like_max=max(feels) if feels else None,
        precipitation_sum=round(precipitation, 1),
        precipitation_probability=(
            round(max(probabilities) * 100) if probabilities else None
        ),
        wind_speed_max=max(winds) if winds else None,
        wind_gust_max=max(gusts) if gusts else None,
        description=top_description,
    )


# ---------------------------------------------------------------------------
# Помощники
# ---------------------------------------------------------------------------


def _kmh(speed_ms: object) -> float | None:
    """Переводит скорость ветра из м/с в км/ч."""
    value = to_float(speed_ms)
    return None if value is None else round(value * MS_TO_KMH, 1)


def _positive(values: list[float | None]) -> list[float]:
    """Отбрасывает пустые значения, оставляя только числа."""
    return [value for value in values if value is not None]


def _entry_description(entry: dict) -> str | None:
    weather = entry.get("weather")
    if isinstance(weather, list) and weather:
        text = (weather[0] or {}).get("description")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


def _precipitation(entry: dict) -> float:
    """Осадки за интервал наблюдения: суммирует блоки rain и snow."""
    total = 0.0
    for key in ("rain", "snow"):
        block = entry.get(key)
        if isinstance(block, dict):
            for value in block.values():
                number = to_float(value)
                if number is not None:
                    total += number
    return round(total, 2)


def _numbers(entries: list[dict], path: tuple[str, ...]) -> list[float]:
    """Собирает числовые значения по пути вида ('main', 'temp') из записей."""
    values: list[float] = []
    for entry in entries:
        node: object = entry
        for key in path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        number = to_float(node)
        if number is not None:
            values.append(number)
    return values


def _sum_3h(entries: list[dict], key: str) -> float:
    """Суммирует осадки '3h' по всем трёхчасовым записям."""
    total = 0.0
    for entry in entries:
        block = entry.get(key)
        if isinstance(block, dict):
            number = to_float(block.get("3h"))
            if number is not None:
                total += number
    return total


def _observed_at(epoch: object, shift: object = 0.0) -> datetime | None:
    """Время наблюдения в поясе точки (фиксированное смещение UTC±N)."""
    value = to_float(epoch)
    if value is None:
        return None
    offset = to_float(shift) or 0.0
    return datetime.fromtimestamp(value, tz=timezone(timedelta(seconds=offset)))


def _entry_local_moment(entry: dict, shift: float) -> datetime | None:
    value = to_float(entry.get("dt"))
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=UTC) + timedelta(seconds=shift)


def _local_date(payload: dict) -> date | None:
    value = to_float(payload.get("dt"))
    if value is None:
        return None
    shift = to_float(payload.get("timezone")) or 0.0
    moment = datetime.fromtimestamp(value, tz=UTC) + timedelta(seconds=shift)
    return moment.date()


def _clock(epoch: object, shift: object) -> str | None:
    """Локальное время рассвета/заката в виде 'HH:MM'."""
    value = to_float(epoch)
    if value is None:
        return None
    offset = to_float(shift) or 0.0
    moment = datetime.fromtimestamp(value, tz=UTC) + timedelta(seconds=offset)
    return moment.strftime("%H:%M")
