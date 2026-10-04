"""Проверки разбора ответов площадок на сохранённых фикстурах.

Фикстуры open_meteo_forecast.json, open_meteo_marine.json и wttr_in.json —
реальные ответы API для точки 43.427292, 39.917107.
Фикстуры openweathermap_*.json — синтетические (по схеме документации),
так как реальный ключ OpenWeatherMap недоступен; см. поле "_note" внутри.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

from helpers import load_fixture

from app.config import Settings
from app.models import SourceResult
from app.sources import build_sources, collect_all
from app.sources.base import SourceError, WeatherSource
from app.sources.open_meteo import parse as parse_open_meteo
from app.sources.open_meteo_marine import parse as parse_marine
from app.sources.openweathermap import parse as parse_openweathermap
from app.sources.wttr_in import parse as parse_wttr
from app.wwo import wwo_description


def test_open_meteo_current_and_days(settings: Settings) -> None:
    """Open-Meteo: текущие значения совпадают с ответом API, прогноз — 3 суток."""
    payload = load_fixture("open_meteo_forecast.json")
    result = parse_open_meteo(payload, settings)

    assert result.ok and result.source == "Open-Meteo"
    assert result.current is not None
    assert result.current.temperature == payload["current"]["temperature_2m"]
    assert result.current.humidity == payload["current"]["relative_humidity_2m"]
    assert result.current.pressure == payload["current"]["pressure_msl"]
    assert result.current.wind_speed == payload["current"]["wind_speed_10m"]
    assert result.current.description == "небольшой ливень"  # WMO 80 в фикстуре
    assert result.current.observed_at is not None
    assert result.current.observed_at.tzinfo is not None

    # источник отдаёт весь запрошенный горизонт; ограничение делает агрегация
    assert len(result.days) == len(payload["daily"]["time"])
    first = result.days[0]
    assert first.day == date(2026, 9, 29)
    assert first.temp_min == payload["daily"]["temperature_2m_min"][0]
    assert first.temp_max == payload["daily"]["temperature_2m_max"][0]
    assert first.sunrise == "06:15"
    assert first.sunset == "18:05"


def test_open_meteo_marine(settings: Settings) -> None:
    """Open-Meteo Marine: температура воды, волна и прогноз волнения."""
    payload = load_fixture("open_meteo_marine.json")
    result = parse_marine(payload, settings)

    assert result.ok and result.source == "Open-Meteo Marine"
    assert result.marine_current is not None
    assert result.marine_current.sea_temperature == payload["current"]["sea_surface_temperature"]
    assert result.marine_current.wave_height == payload["current"]["wave_height"]
    assert result.marine_current.wave_period == payload["current"]["wave_period"]
    assert result.marine_current.wave_direction == payload["current"]["wave_direction"]

    assert len(result.marine_days) == len(payload["daily"]["time"])
    first = result.marine_days[0]
    assert first.day == date(2026, 9, 29)
    assert first.wave_height_max == payload["daily"]["wave_height_max"][0]
    assert first.wave_direction_dominant == payload["daily"]["wave_direction_dominant"][0]


def test_wttr_in_descriptions_are_russian(settings: Settings) -> None:
    """wttr.in: описания переводятся с кодов WWO, время наблюдения — 24-часовое."""
    payload = load_fixture("wttr_in.json")
    result = parse_wttr(payload, settings)

    assert result.ok and result.source == "wttr.in"
    assert result.current is not None
    assert result.current.temperature == 16.0
    assert result.current.wind_speed == 6.0
    assert result.current.description == "небольшой ливневый дождь"
    assert result.current.observed_at is not None
    assert result.current.observed_at.strftime("%H:%M") == "20:28"

    assert len(result.days) == len(payload["weather"])
    first = result.days[0]
    assert first.day == date(2026, 9, 29)
    assert first.temp_min == 16.0
    assert first.temp_max == 22.0
    assert first.sunrise == "06:15"
    assert first.sunset == "18:05"
    assert first.precipitation_sum is not None and first.precipitation_sum > 0
    hourly_rain = [
        float(item["chanceofrain"]) for item in payload["weather"][0]["hourly"]
    ]
    assert first.precipitation_probability == max(hourly_rain)
    expected_descriptions = {
        wwo_description(item["weatherCode"]) for item in payload["weather"][0]["hourly"]
    }
    assert first.description in expected_descriptions


def test_openweathermap_current(settings: Settings) -> None:
    """OpenWeatherMap: м/с переводятся в км/ч, время — в UTC-момент."""
    current = load_fixture("openweathermap_current.json")
    forecast = load_fixture("openweathermap_forecast.json")
    result = parse_openweathermap(current, forecast, settings)

    assert result.ok and result.source == "OpenWeatherMap"
    assert result.current is not None
    assert result.current.temperature == 18.4
    assert result.current.feels_like == 18.9
    assert result.current.humidity == 90.0
    assert result.current.pressure == 1014.0
    assert result.current.wind_speed == 9.0
    assert result.current.wind_gust == 14.8
    assert result.current.wind_direction == 115.0
    assert result.current.cloud_cover == 92.0
    assert result.current.precipitation == 0.4
    assert result.current.description == "небольшой дождь"
    assert result.current.observed_at == datetime(2026, 9, 29, 20, 0, tzinfo=timezone.utc)


def test_openweathermap_daily_aggregation(settings: Settings) -> None:
    """OpenWeatherMap: трёхчасовые записи сворачиваются в суточные значения."""
    current = load_fixture("openweathermap_current.json")
    forecast = load_fixture("openweathermap_forecast.json")
    result = parse_openweathermap(current, forecast, settings)

    assert [day.day for day in result.days] == [
        date(2026, 9, 29),
        date(2026, 9, 30),
        date(2026, 10, 1),
    ]

    today = result.days[0]
    assert today.temp_min == 18.9
    assert today.precipitation_sum == 0.3
    assert today.precipitation_probability == 60.0
    assert today.wind_speed_max == 7.6
    assert today.wind_gust_max == 12.2
    # рассвет и закат берутся из ответа /weather (sys + часовой сдвиг города)
    assert today.sunrise == "06:15"
    assert today.sunset == "18:05"

    tomorrow = result.days[1]
    assert tomorrow.temp_min == 17.0
    assert tomorrow.temp_max == 18.7
    assert tomorrow.precipitation_sum == 1.6
    assert tomorrow.precipitation_probability == 70.0
    assert tomorrow.wind_speed_max == 15.1
    assert tomorrow.wind_gust_max == 22.3


def test_build_sources_skips_openweathermap_without_key() -> None:
    """Без ключа OWM площадка не опрашивается и попадает в список пропущенных."""
    settings = Settings(city="Адлер", openweather_api_key="")
    enabled, skipped = build_sources(settings)

    names = [source.name for source in enabled]
    assert "OpenWeatherMap" not in names
    assert names == ["Open-Meteo", "wttr.in", "Open-Meteo Marine", "USGS"]
    assert len(skipped) == 1
    assert "OPENWEATHER_API_KEY" in skipped[0]


def test_build_sources_skips_usgs_when_earthquakes_disabled() -> None:
    """При EARTHQUAKES_ENABLED=false площадка USGS не опрашивается."""
    settings = Settings(city="Адлер", earthquakes_enabled=False)
    enabled, skipped = build_sources(settings)

    names = [source.name for source in enabled]
    assert "USGS" not in names
    assert "Open-Meteo" in names
    assert any("EARTHQUAKES_ENABLED" in reason for reason in skipped)


class _StubSource(WeatherSource):
    """Площадка-заглушка для проверки обработки успехов и ошибок."""

    def __init__(self, name: str, result: SourceResult | None = None, error: str = "") -> None:
        super().__init__(Settings())
        self.name = name
        self._result = result
        self._error = error

    async def collect(self, client) -> SourceResult:  # noqa: ANN001 - тест
        if self._error:
            raise SourceError(self._error)
        assert self._result is not None
        return self._result


def test_collect_all_keeps_errors_as_results() -> None:
    """collect_all не падает из-за одной площадки: ошибка становится полем error."""
    ok_source = _StubSource("Успех", result=SourceResult(source="Успех"))
    bad_source = _StubSource("Сбой", error="HTTP 401")
    crashing_source = _StubSource("Падение", error="")

    async def fake_collect(client) -> SourceResult:  # noqa: ANN001 - тест
        raise ValueError("поломанный ответ")

    crashing_source.collect = fake_collect  # type: ignore[method-assign]

    results, skipped = asyncio.run(
        collect_all(Settings(), sources=[ok_source, bad_source, crashing_source])
    )

    assert skipped == []
    assert [result.source for result in results] == ["Успех", "Сбой", "Падение"]
    assert results[0].ok and results[0].error is None
    assert results[1].error == "HTTP 401"
    assert results[2].error is not None and "ValueError" in results[2].error

