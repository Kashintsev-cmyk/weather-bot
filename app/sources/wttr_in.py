"""Площадка wttr.in (данные World Weather Online): точка, текущие условия, 3 суток."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

import httpx

from ..codes import wmo_from_wwo
from ..config import Settings
from ..models import CurrentWeather, DayForecast, HourlyPoint, SourceResult
from ..timeutil import local_now, safe_zone
from ..wwo import translate_en, wwo_description
from .base import SourceError, WeatherSource, parse_date, to_float

API_URL = "https://wttr.in/{lat},{lon}"
NAME = "wttr.in"
MAX_HOURS = 48


class WttrInSource(WeatherSource):
    """wttr.in отдаёт описания только по-английски — переводим через коды WWO."""

    name = NAME

    async def collect(self, client: httpx.AsyncClient) -> SourceResult:
        url = self.build_url(self.settings)
        payload = await self._get_json(
            client, url, params={"format": "j1", "lang": "ru"}
        )
        try:
            return parse(payload, self.settings)
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceError(f"неожиданный формат ответа: {exc!r}") from exc

    @staticmethod
    def build_url(settings: Settings) -> str:
        """URL запроса к wttr.in для точки прогноза."""
        return API_URL.format(lat=settings.lat, lon=settings.lon)


def parse(payload: dict, settings: Settings) -> SourceResult:
    """Нормализует ответ wttr.in (format=j1) в SourceResult."""
    conditions = payload.get("current_condition") or []
    days_payload = [entry for entry in (payload.get("weather") or []) if isinstance(entry, dict)]

    condition = conditions[0] if conditions and isinstance(conditions[0], dict) else {}
    current = _parse_current(condition, days_payload, settings)
    days = [day for day in (_parse_day(entry) for entry in days_payload) if day is not None]

    return SourceResult(
        source=NAME,
        current=current,
        days=days[: settings.forecast_days],
        hourly=_parse_hourly(days_payload, settings),
    )


def _parse_hourly(days_payload: list[dict], settings: Settings) -> list[HourlyPoint]:
    """Разбирает почасовые записи wttr.in (шаг 3 часа, поле time — 0..2100)."""
    points: list[HourlyPoint] = []
    for entry in days_payload:
        day = parse_date(entry.get("date"))
        if day is None:
            continue
        hourly = [item for item in (entry.get("hourly") or []) if isinstance(item, dict)]
        for item in hourly:
            hour = _hour_of(item.get("time"))
            if hour is None:
                continue
            chances = _numbers([item], "chanceofrain") + _numbers([item], "chanceofsnow")
            points.append(
                HourlyPoint(
                    time=datetime(
                        day.year,
                        day.month,
                        day.day,
                        hour,
                        tzinfo=safe_zone(settings.timezone),
                    ),
                    source=NAME,
                    span_hours=3.0,
                    temperature=to_float(item.get("tempC")),
                    feels_like=to_float(item.get("FeelsLikeC")),
                    precipitation=to_float(item.get("precipMM")),
                    precipitation_probability=max(chances) if chances else None,
                    wind_speed=to_float(item.get("windspeedKmph")),
                    wind_gust=to_float(item.get("WindGustKmph")),
                    wind_direction=to_float(item.get("winddirDegree")),
                    cloud_cover=to_float(item.get("cloudcover")),
                    humidity=to_float(item.get("humidity")),
                    weather_code=wmo_from_wwo(item.get("weatherCode")),
                    description=_describe(item),
                )
            )
    return points[:MAX_HOURS]


def _hour_of(value: object) -> int | None:
    """'600' -> 6, '2100' -> 21 (значения wttr.in кратны 100)."""
    raw = to_float(value)
    if raw is None:
        return None
    hour = int(raw) // 100
    return hour if 0 <= hour <= 23 else None


def _parse_current(
    condition: dict, days_payload: list[dict], settings: Settings
) -> CurrentWeather:
    return CurrentWeather(
        source=NAME,
        observed_at=_observation_moment(condition, days_payload, settings),
        temperature=to_float(condition.get("temp_C")),
        feels_like=to_float(condition.get("FeelsLikeC")),
        humidity=to_float(condition.get("humidity")),
        pressure=to_float(condition.get("pressure")),
        wind_speed=to_float(condition.get("windspeedKmph")),
        wind_gust=to_float(condition.get("WindGustKmph")),
        wind_direction=to_float(condition.get("winddirDegree")),
        cloud_cover=to_float(condition.get("cloudcover")),
        precipitation=to_float(condition.get("precipMM")),
        description=_describe(condition),
    )


def _parse_day(entry: dict) -> DayForecast | None:
    day = parse_date(entry.get("date"))
    if day is None:
        return None

    hourly = [item for item in (entry.get("hourly") or []) if isinstance(item, dict)]
    temperatures = _numbers(hourly, "tempC")
    feels = _numbers(hourly, "FeelsLikeC")
    winds = _numbers(hourly, "windspeedKmph")
    gusts = _numbers(hourly, "WindGustKmph")
    probabilities = []
    for item in hourly:
        vals = _numbers([item], "chanceofrain") + _numbers([item], "chanceofsnow")
        if vals:
            probabilities.append(max(vals))

    precipitation = sum(_numbers(hourly, "precipMM"))
    descriptions = [text for text in (_describe(item) for item in hourly) if text]
    top_description = Counter(descriptions).most_common(1)[0][0] if descriptions else None

    astronomy = entry.get("astronomy") or []
    astro = astronomy[0] if astronomy and isinstance(astronomy[0], dict) else {}

    temp_min = to_float(entry.get("mintempC"))
    temp_max = to_float(entry.get("maxtempC"))
    if temp_min is None and temperatures:
        temp_min = min(temperatures)
    if temp_max is None and temperatures:
        temp_max = max(temperatures)

    return DayForecast(
        source=NAME,
        day=day,
        temp_min=temp_min,
        temp_max=temp_max,
        feels_like_max=max(feels) if feels else None,
        precipitation_sum=round(precipitation, 1),
        precipitation_probability=max(probabilities) if probabilities else None,
        wind_speed_max=max(winds) if winds else None,
        wind_gust_max=max(gusts) if gusts else None,
        uv_index_max=to_float(entry.get("uvIndex")),
        description=top_description,
        sunrise=_clock(astro.get("sunrise")),
        sunset=_clock(astro.get("sunset")),
    )


# ---------------------------------------------------------------------------
# Помощники
# ---------------------------------------------------------------------------


def _describe(entry: dict) -> str | None:
    """Русское описание: сначала по коду WWO, затем — перевод английского текста."""
    by_code = wwo_description(entry.get("weatherCode"))
    if by_code:
        return by_code
    return translate_en(_description_text(entry))


def _description_text(entry: dict) -> str | None:
    descriptions = entry.get("weatherDesc")
    if isinstance(descriptions, list) and descriptions:
        value = (descriptions[0] or {}).get("value")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _numbers(entries: list[dict], key: str) -> list[float]:
    """Числовые значения поля по всем записям."""
    values: list[float] = []
    for entry in entries:
        number = to_float(entry.get(key))
        if number is not None:
            values.append(number)
    return values


def _observation_moment(
    condition: dict, days_payload: list[dict], settings: Settings
) -> datetime | None:
    """Время наблюдения: '08:28 PM' + дата первого прогнозного дня.

    wttr.in кэширует ответы, поэтому сразу после местной полуночи
    weather[0].date может быть вчерашним. Если построенный момент
    оказывается больше чем на 12 часов в прошлом, добавляем сутки —
    это ближе к реальному наблюдению.
    """
    clock = _clock(condition.get("observation_time"))
    if clock is None:
        return None
    zone = safe_zone(settings.timezone)
    now = local_now(settings.timezone)
    day = parse_date(days_payload[0].get("date")) if days_payload else None
    day = day or now.date()
    hour, minute = (int(part) for part in clock.split(":"))
    moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)

    # wttr.in кэширует ответы и ставит weather[0].date = сегодня,
    # а observation_time может относиться ко вчерашнему вечеру —
    # тогда момент уезжает в будущее. Если он далеко от now в любую
    # сторону, пробуем ±1 сутки и берём ближайший.
    if abs((now - moment).total_seconds()) > 12 * 3600:
        best = moment
        best_diff = abs((now - moment).total_seconds())
        for shift in (timedelta(days=-1), timedelta(days=1)):
            candidate = moment + shift
            diff = abs((now - candidate).total_seconds())
            if diff < best_diff:
                best = candidate
                best_diff = diff
        moment = best
    return moment


def _clock(value: object) -> str | None:
    """Приводит '06:15 AM' / '08:28 PM' к 24-часовому виду 'HH:MM'."""
    if not isinstance(value, str):
        return None
    text = value.strip().upper()
    meridiem = text[-2:] if text.endswith(("AM", "PM")) else ""
    if meridiem:
        text = text[:-2].strip()
    hours_text, _, minutes_text = text.partition(":")
    try:
        hour = int(hours_text)
        minute = int(minutes_text[:2])
    except ValueError:
        return None
    if meridiem == "PM" and hour != 12:
        hour += 12
    if meridiem == "AM" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"
