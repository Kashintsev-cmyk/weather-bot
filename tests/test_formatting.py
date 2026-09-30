"""Проверки текстового представления обобщённой сводки."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.aggregate import build_report
from app.config import Settings
from app.formatting import (
    SECTION_CURRENT,
    SECTION_FORECAST,
    SECTION_MARINE,
    SECTION_SEA_FORECAST,
    SECTION_SOURCES,
    _compass,
    _date_long,
    _day_label,
    _num,
    _temp,
    _temp_range,
    format_report,
)
from app.models import (
    CurrentWeather,
    DayForecast,
    MarineCurrent,
    MarineDay,
    SourceResult,
)

NOW = datetime(2026, 9, 29, 23, 30, tzinfo=timezone(timedelta(hours=3)))
DAY = date(2026, 9, 30)


@pytest.mark.parametrize(
    ("degrees", "expected"),
    [(122.0, "ВЮВ"), (219.0, "ЮЗ"), (0.0, "С"), (359.0, "С"), (90.0, "В"), (None, "н/д")],
)
def test_compass(degrees: float | None, expected: str) -> None:
    assert _compass(degrees) == expected


def test_temperature_helpers() -> None:
    assert _temp(19.0) == "+19 °C"
    assert _temp(-3.4) == "-3 °C"
    assert _temp(None) == "н/д"
    assert _temp_range(16.0, 20.0) == "+16…+20 °C"
    assert _temp_range(18.9, 18.9) == "+19 °C"
    assert _temp_range(18.4, 18.6) == "+18…+19 °C"
    assert _temp_range(None, None) == "н/д"
    assert _temp_range(None, 21.0) == "до +21 °C"


def test_number_and_date_helpers() -> None:
    assert _num(4.06, 1) == "4.1"
    assert _num(7.4) == "7"
    assert _num(None) == "н/д"
    assert _date_long(date(2026, 9, 29)) == "29 сентября 2026"
    assert _day_label(date(2026, 9, 29), date(2026, 9, 29)) == "сегодня, 29 сентября"
    assert _day_label(date(2026, 9, 30), date(2026, 9, 29)) == "завтра, 30 сентября"
    assert _day_label(date(2026, 10, 1), date(2026, 9, 29)) == "четверг, 1 октября"


def _sample_report(settings: Settings):
    """Отчёт из двух погодных площадок и одной морской."""
    results = [
        SourceResult(
            source="Open-Meteo",
            current=CurrentWeather(
                source="Open-Meteo",
                observed_at=NOW,
                temperature=16.0,
                feels_like=17.0,
                humidity=92.0,
                pressure=1015.0,
                wind_speed=6.0,
                wind_gust=12.0,
                wind_direction=122.0,
                cloud_cover=90.0,
                precipitation=0.2,
                description="небольшой дождь",
            ),
            days=[
                DayForecast(
                    source="Open-Meteo",
                    day=DAY,
                    temp_min=17.0,
                    temp_max=20.0,
                    feels_like_max=21.0,
                    precipitation_sum=5.0,
                    precipitation_probability=80.0,
                    wind_speed_max=15.0,
                    wind_gust_max=25.0,
                    uv_index_max=3.5,
                    description="небольшой дождь",
                    sunrise="06:16",
                    sunset="18:03",
                )
            ],
        ),
        SourceResult(
            source="wttr.in",
            current=CurrentWeather(
                source="wttr.in",
                observed_at=NOW - timedelta(hours=3),
                temperature=19.0,
                feels_like=20.0,
                humidity=90.0,
                pressure=1016.0,
                wind_speed=9.0,
                wind_gust=14.0,
                wind_direction=130.0,
                cloud_cover=93.0,
                precipitation=1.6,
                description="пасмурно",
            ),
            days=[
                DayForecast(
                    source="wttr.in",
                    day=DAY,
                    temp_min=16.0,
                    temp_max=22.0,
                    precipitation_sum=8.0,
                    precipitation_probability=90.0,
                    wind_speed_max=17.0,
                    wind_gust_max=28.0,
                    description="пасмурно",
                    sunrise="06:15",
                    sunset="18:03",
                )
            ],
        ),
        SourceResult(
            source="Open-Meteo Marine",
            marine_current=MarineCurrent(
                source="Open-Meteo Marine",
                observed_at=NOW,
                sea_temperature=23.0,
                wave_height=0.5,
                wave_period=4.1,
                wave_direction=219.0,
                swell_height=0.4,
            ),
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
        ),
    ]
    return build_report(settings, results, now=NOW)


def test_format_report_full_text(settings: Settings) -> None:
    """Полная сводка содержит шапку, текущие условия, прогноз и море."""
    report = _sample_report(settings)
    text = format_report(report)
    current = report.current
    day = report.days[0]
    assert current is not None and report.marine is not None

    assert text.startswith("Адлер — обобщённая сводка погоды и моря")
    assert "Обновлено: 29 сентября 2026, 23:30 (МСК)" in text
    assert "Точка: 43.427292, 39.917107" in text
    assert "Источники: Open-Meteo, wttr.in, Open-Meteo Marine (3 из 3)" in text

    assert f"Сейчас: {_temp(current.temperature.median)}" in text
    assert "ощущается как" in text
    assert "Площадки дают +16…+19 °C" in text
    assert "Влажность 91%" in text
    assert f"ветер {_num(current.wind_speed.median)} км/ч" in text
    assert "давление 1015…1016 гПа" in text

    assert "Море: вода +23 °C, волна 0.5 м." in text
    assert "Период волны 4.1 с; направление ЮЗ (219°); зыбь 0.4 м." in text

    assert "Прогноз по дням (обобщённо):" in text
    assert f"• завтра, 30 сентября — {_temp_range(day.temp_min.median, day.temp_max.median)}" in text
    assert f"Ощущается до {_temp(day.feels_like_max.median)}" in text
    assert "осадки до 6.5 мм (вероятность 85%)" in text
    assert f"рассвет {day.sunrise}" in text and f"закат {day.sunset}" in text
    assert "Описания по площадкам: Open-Meteo — небольшой дождь; wttr.in — пасмурно." in text

    assert "Море по дням:" in text
    assert "Площадки и разброс данных:" in text
    assert "Разброс по источникам:" in text
    assert "— Open-Meteo: +16 °C, небольшой дождь, наблюдение 23:30" in text


def test_format_report_respects_sections(settings: Settings) -> None:
    """Можно запрашивать только нужные разделы сводки."""
    report = _sample_report(settings)

    sea_text = format_report(report, (SECTION_MARINE,))
    assert "Море: вода +23 °C" in sea_text
    assert "Сейчас:" not in sea_text
    assert "Прогноз по дням" not in sea_text
    assert "Площадки и разброс данных" not in sea_text

    weather_text = format_report(report, (SECTION_CURRENT, SECTION_FORECAST))
    assert "Сейчас:" in weather_text
    assert "Прогноз по дням" in weather_text
    assert "Море" not in weather_text
    assert "Площадки и разброс данных" not in weather_text

    sea_forecast = format_report(report, (SECTION_SEA_FORECAST,))
    assert "Море по дням:" in sea_forecast
    assert "волна до 1.1 м" in sea_forecast

    sources = format_report(report, (SECTION_SOURCES,))
    assert "Площадки и разброс данных:" in sources
    assert "Сейчас:" not in sources


def test_format_report_without_data(settings: Settings) -> None:
    """Пустой отчёт даёт понятный текст вместо исключения."""
    text = format_report(build_report(settings, [], now=NOW))

    assert "ни одна площадка не была опрошена" in text
    assert "Текущие условия: площадки не отдали данные." in text
    assert "Прогноз по дням: данных нет." in text
    assert "Ни одна площадка не отдала данные" in text
    assert "Море" not in text

