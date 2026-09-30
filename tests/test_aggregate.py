"""Проверки обобщения данных: медиана, разброс, мода описаний, заметки."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.aggregate import build_report, circular_mean, consensus
from app.config import Settings
from app.models import (
    CurrentWeather,
    DayForecast,
    MarineCurrent,
    MarineDay,
    SourceResult,
)

NOW = datetime(2026, 9, 29, 23, 30, tzinfo=timezone(timedelta(hours=3)))
DAY = date(2026, 9, 30)


def test_consensus_uses_median_and_reports_spread() -> None:
    result = consensus({"A": 16.0, "B": 19.0, "C": 24.0})

    assert result.median == 19.0
    assert result.low == 16.0
    assert result.high == 24.0
    assert result.spread == 8.0
    assert result.by_source == {"A": 16.0, "B": 19.0, "C": 24.0}
    assert result.available


def test_consensus_without_values_is_unavailable() -> None:
    result = consensus({})

    assert not result.available
    assert result.median is None
    assert result.spread is None


@pytest.mark.parametrize(
    ("degrees", "expected"),
    [([350.0, 10.0], 0.0), ([90.0, 90.0], 90.0), ([0.0], 0.0), ([180.0, 200.0], 190.0)],
)
def test_circular_mean(degrees: list[float], expected: float) -> None:
    value = circular_mean(degrees)

    assert value is not None
    assert min(abs(value - expected), 360 - abs(value - expected)) < 0.5


def test_circular_mean_empty() -> None:
    assert circular_mean([]) is None


def _current(
    source: str,
    temperature: float,
    description: str,
    wind_speed: float,
    direction: float = 120.0,
) -> CurrentWeather:
    return CurrentWeather(
        source=source,
        observed_at=NOW,
        temperature=temperature,
        feels_like=temperature + 1.0,
        humidity=90.0,
        pressure=1015.0,
        wind_speed=wind_speed,
        wind_gust=wind_speed + 4.0,
        wind_direction=direction,
        cloud_cover=90.0,
        precipitation=0.2,
        description=description,
    )


def _day(
    source: str,
    temp_min: float,
    temp_max: float,
    sunrise: str,
    description: str,
) -> DayForecast:
    return DayForecast(
        source=source,
        day=DAY,
        temp_min=temp_min,
        temp_max=temp_max,
        feels_like_max=temp_max + 1.0,
        precipitation_sum=2.0,
        precipitation_probability=70.0,
        wind_speed_max=15.0,
        wind_gust_max=25.0,
        uv_index_max=3.0,
        description=description,
        sunrise=sunrise,
        sunset="18:03",
    )


def _marine(source: str, sea_temperature: float, wave: float) -> MarineCurrent:
    return MarineCurrent(
        source=source,
        observed_at=NOW,
        sea_temperature=sea_temperature,
        wave_height=wave,
        wave_period=4.0,
        wave_direction=220.0,
        swell_height=wave - 0.1,
    )


def test_build_report_averages_current_conditions(settings: Settings) -> None:
    """Медиана и разброс считаются по всем площадкам, время — в поясе города."""
    results = [
        SourceResult(
            source="Open-Meteo",
            current=_current("Open-Meteo", 16.0, "небольшой дождь", 6.0),
        ),
        SourceResult(
            source="wttr.in",
            current=_current("wttr.in", 19.0, "пасмурно", 9.0, direction=140.0),
        ),
    ]

    report = build_report(settings, results, skipped=[], now=NOW)

    assert report.generated_at == NOW
    assert report.sources_ok == ["Open-Meteo", "wttr.in"]
    assert report.current is not None
    current = report.current
    assert current.temperature.median == 17.5
    assert current.temperature.spread == 3.0
    assert current.wind_speed.median == 7.5
    assert current.wind_direction.median is not None
    assert current.description in {"небольшой дождь", "пасмурно"}
    assert current.observed_at is not None
    assert current.observed_at.utcoffset() == timedelta(hours=3)
    # расхождение ровно на пороге (3 °C) — попадает в примечания
    assert any("Расхождение температур" in note for note in report.notes)


def test_build_report_merges_days_by_date(settings: Settings) -> None:
    """Прогнозы площадок на одну дату сливаются в один обобщённый день."""
    results = [
        SourceResult(
            source="Open-Meteo",
            days=[_day("Open-Meteo", 17.0, 20.0, "06:16", "небольшой дождь")],
        ),
        SourceResult(
            source="wttr.in",
            days=[_day("wttr.in", 16.0, 22.0, "06:15", "пасмурно")],
        ),
    ]

    report = build_report(settings, results, now=NOW)

    assert len(report.days) == 1
    day = report.days[0]
    assert day.day == DAY
    assert day.temp_min.median == 16.5
    assert day.temp_max.median == 21.0
    assert day.temp_min.low == 16.0
    assert day.temp_max.high == 22.0
    assert day.descriptions == {"Open-Meteo": "небольшой дождь", "wttr.in": "пасмурно"}
    assert day.sunrise in {"06:15", "06:16"}
    assert day.precipitation_probability.median == 70.0


def test_build_report_limits_days_to_forecast_horizon(settings: Settings) -> None:
    """В сводку попадает только запрошенное число дней."""
    days = [
        DayForecast(source="Open-Meteo", day=DAY + timedelta(days=offset))
        for offset in range(4)
    ]

    report = build_report(settings, [SourceResult(source="Open-Meteo", days=days)], now=NOW)

    assert len(report.days) == settings.forecast_days


def test_build_report_collects_marine_data(settings: Settings) -> None:
    """Морские площадки обобщаются отдельно от погодных."""
    results = [
        SourceResult(
            source="Open-Meteo Marine",
            marine_current=_marine("Open-Meteo Marine", 23.0, 0.5),
            marine_days=[
                MarineDay(
                    source="Open-Meteo Marine",
                    day=DAY,
                    wave_height_max=1.1,
                    wave_period_max=5.5,
                    wave_direction_dominant=227.0,
                    sea_temp_min=22.7,
                    sea_temp_max=23.0,
                )
            ],
        )
    ]

    report = build_report(settings, results, now=NOW)

    assert report.current is None
    assert report.marine is not None
    assert report.marine.sea_temperature.median == 23.0
    assert report.marine.wave_height.median == 0.5
    assert report.marine.swell_height.median == 0.4
    assert len(report.marine_days) == 1
    assert report.marine_days[0].wave_height_max.median == 1.1
    assert report.marine_days[0].wave_direction_dominant.median == 227.0


def test_build_report_handles_failures_and_skips(settings: Settings) -> None:
    """Ошибки и пропуски площадок попадают в отчёт и примечания."""
    results = [
        SourceResult(
            source="Open-Meteo",
            current=_current("Open-Meteo", 17.0, "пасмурно", 6.0),
        ),
        SourceResult(source="OpenWeatherMap", error="HTTP 401"),
    ]
    skipped = ["OpenWeatherMap пропущен: не задан ключ OPENWEATHER_API_KEY"]

    report = build_report(settings, results, skipped=skipped, now=NOW)

    assert report.sources_ok == ["Open-Meteo"]
    assert report.sources_failed == [("OpenWeatherMap", "HTTP 401")]
    assert report.sources_skipped == skipped
    assert any("HTTP 401" in note for note in report.notes)
    assert any("OPENWEATHER_API_KEY" in note for note in report.notes)


def test_build_report_without_any_data(settings: Settings) -> None:
    """Пустой набор результатов не ломает сборку отчёта."""
    report = build_report(settings, [], now=NOW)

    assert report.current is None
    assert report.days == []
    assert report.marine is None
    assert report.sources_ok == []
    assert any("Ни одна площадка" in note for note in report.notes)

