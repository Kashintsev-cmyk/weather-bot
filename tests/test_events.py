"""Проверки детектора событий: пороги, окна, уровни и дедупликация ключей."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from helpers import hours, marine_hours, quake, report

from app.config import Settings
from app.events import (
    KIND_COLD_SNAP,
    KIND_FOG,
    KIND_FROST,
    KIND_HAIL,
    KIND_HURRICANE,
    KIND_MARINE_STORM,
    KIND_QUAKE,
    KIND_RAIN,
    KIND_SNOW,
    KIND_SQUALL,
    KIND_THUNDER,
    KIND_TSUNAMI,
    LEVEL_DANGER,
    LEVEL_INFO,
    LEVEL_WARNING,
    detect_events,
    upcoming_events,
)
from app.formatting import SECTION_EVENTS, SECTION_FORECAST, format_report

NOW = datetime(2026, 9, 30, 7, 0, tzinfo=timezone(timedelta(hours=3)))


def _kinds(events: list) -> list[str]:
    return [event.kind for event in events]


def _by_kind(events: list, kind: str):
    for event in events:
        if event.kind == kind:
            return event
    return None


def test_rain_episode_uses_thresholds_and_span(settings: Settings) -> None:
    """Два подряд мокрых часа собираются в один эпизод с общим окном."""
    built = hours(
        NOW,
        [
            {"shift": 0, "probability": 30},
            {"shift": 1, "precipitation": 0.6, "probability": 80, "weather_code": 61},
            {"shift": 2, "precipitation": 1.2, "probability": 90, "weather_code": 63},
            {"shift": 3, "probability": 20},
        ],
    )
    events = detect_events(report(settings, built, now=NOW), settings, now=NOW)

    rain = _by_kind(events, KIND_RAIN)
    assert rain is not None
    assert rain.level == LEVEL_WARNING
    assert rain.starts_at == NOW + timedelta(hours=1)
    assert rain.ends_at == NOW + timedelta(hours=3)
    assert rain.window_label(settings.timezone) == "08:00–10:00"
    assert "1.2 мм/ч" in rain.details
    assert rain.key == "rain:2026093008"
    assert _kinds(events) == [KIND_RAIN]


def test_heavy_rain_and_thunder_get_danger_level(settings: Settings) -> None:
    """Ливень, гроза с градом и ураганный ветер поднимают уровень до danger."""
    built = hours(
        NOW,
        [
            {"shift": 1, "precipitation": 6.0, "probability": 95, "weather_code": 65},
            {"shift": 2, "weather_code": 95, "wind_gust": 78.0, "cape": 1500.0},
            {"shift": 3, "weather_code": 99, "wind_gust": 95.0, "cape": 2000.0},
        ],
    )
    events = detect_events(report(settings, built, now=NOW), settings, now=NOW)

    rain = _by_kind(events, KIND_RAIN)
    thunder = _by_kind(events, KIND_THUNDER)
    hail = _by_kind(events, KIND_HAIL)
    hurricane = _by_kind(events, KIND_HURRICANE)
    assert rain is not None and rain.level == LEVEL_DANGER
    assert thunder is not None and thunder.level == LEVEL_DANGER
    assert "порывы до 95 км/ч" in thunder.details
    assert hail is not None and hail.level == LEVEL_DANGER
    assert hurricane is not None and hurricane.level == LEVEL_DANGER


def test_squall_threshold_is_between_warning_and_hurricane(settings: Settings) -> None:
    """Порывы выше порога шквала — warning, выше ураганного — danger."""
    built = hours(
        NOW,
        [
            {"shift": 1, "wind_gust": settings.notify_gust_kmh + 5.0, "weather_code": 3},
            {"shift": 5, "wind_gust": settings.notify_hurricane_gust_kmh + 10.0},
        ],
    )
    events = detect_events(report(settings, built, now=NOW), settings, now=NOW)

    squall = _by_kind(events, KIND_SQUALL)
    hurricane = _by_kind(events, KIND_HURRICANE)
    assert squall is not None and squall.level == LEVEL_WARNING
    assert hurricane is not None and hurricane.level == LEVEL_DANGER
    assert squall.ends_at <= hurricane.starts_at


def test_tornado_risk_needs_thunder_cape_and_wind(settings: Settings) -> None:
    """Риск смерчей помечаем только при грозе, высокой CAPE и сильном ветре."""
    built = hours(
        NOW,
        [
            {"shift": 1, "weather_code": 95, "cape": settings.notify_tornado_cape + 100.0},
            {
                "shift": 2,
                "weather_code": 95,
                "cape": settings.notify_tornado_cape + 100.0,
                "wind_gust": settings.notify_gust_kmh + 20.0,
            },
        ],
    )
    events = detect_events(report(settings, built, now=NOW), settings, now=NOW)

    risk = [event for event in events if event.kind == "tornado"]
    assert len(risk) == 1
    assert risk[0].level == LEVEL_DANGER
    assert risk[0].starts_at == NOW + timedelta(hours=2)


def test_snow_fog_and_probable_fog(settings: Settings) -> None:
    """Снег по коду WMO и туман (по коду и по влажности) попадают в события."""
    built = hours(
        NOW,
        [
            {"shift": 1, "temperature": -2.0, "weather_code": 71, "precipitation": 1.0},
            {"shift": 3, "weather_code": 45},
            {"shift": 5, "weather_code": 0, "humidity": 98.0, "wind_gust": 2.0},
        ],
    )
    events = detect_events(report(settings, built, now=NOW), settings, now=NOW)

    snow = _by_kind(events, KIND_SNOW)
    fogs = [event for event in events if event.kind == KIND_FOG]
    assert snow is not None and snow.level == LEVEL_WARNING
    assert len(fogs) == 2
    assert fogs[0].level == LEVEL_INFO and fogs[0].summary == "Ожидается туман"
    assert fogs[1].level == LEVEL_WARNING and "штиль" in fogs[1].details


def test_heat_and_frost_levels(settings: Settings) -> None:
    """Жара выше порога на 5 °C — danger, заморозки ниже -5 °C — тоже danger."""
    built = hours(
        NOW,
        [
            {"shift": 1, "temperature": settings.notify_heat_c + 6.0},
            {"shift": 4, "temperature": -1.0},
            {"shift": 5, "temperature": -8.0},
        ],
    )
    events = detect_events(report(settings, built, now=NOW), settings, now=NOW)

    heat = _by_kind(events, "heat")
    frost = _by_kind(events, KIND_FROST)
    assert heat is not None and heat.level == LEVEL_DANGER
    assert "до 39 °C" in heat.details
    assert frost is not None and frost.level == LEVEL_DANGER
    assert "минимум -8 °C" in frost.details
    assert frost.starts_at == NOW + timedelta(hours=4)


def test_cold_snap_is_measured_from_peak(settings: Settings) -> None:
    """Резкое похолодание отсчитывается от максимума в окне прогноза."""
    built = hours(
        NOW,
        [
            {"shift": 1, "temperature": 20.0},
            {"shift": 2, "temperature": 20.0},
            {"shift": 3, "temperature": 12.0},
            {"shift": 4, "temperature": 8.0},
        ],
    )
    events = detect_events(report(settings, built, now=NOW), settings, now=NOW)

    snap = _by_kind(events, KIND_COLD_SNAP)
    assert snap is not None
    assert snap.level == LEVEL_WARNING
    assert snap.starts_at == NOW + timedelta(hours=1)
    assert "на 12 °C" in snap.details
    assert "минимум 8 °C" in snap.details


def test_marine_swell_and_storm(settings: Settings) -> None:
    """Волна выше порога — предупреждение, выше штормового — danger."""
    built = marine_hours(
        NOW,
        [
            {"shift": 1, "wave_height": settings.notify_wave_m + 0.3, "wave_period": 8.0},
            {
                "shift": 5,
                "wave_height": settings.notify_wave_severe_m + 0.5,
                "wave_period": 11.0,
            },
        ],
    )
    events = detect_events(
        report(settings, built_marine_hours=built, now=NOW), settings, now=NOW
    )

    storm = _by_kind(events, KIND_MARINE_STORM)
    assert storm is not None and storm.level == LEVEL_DANGER
    assert "волна до 3.0 м" in storm.details
    assert "период до 11 с" in storm.details


def test_recent_quake_and_tsunami_risk(settings: Settings) -> None:
    """Свежие толчки попадают в события; сильный приповерхностный — риск цунами."""
    quakes = [
        quake(NOW - timedelta(hours=3), magnitude=4.2, depth_km=15.0),
        quake(
            NOW - timedelta(hours=5),
            magnitude=settings.tsunami_magnitude + 0.4,
            depth_km=25.0,
        ),
        quake(NOW - timedelta(days=3), magnitude=5.0),
    ]
    events = detect_events(report(settings, quakes=quakes, now=NOW), settings, now=NOW)

    assert _kinds(events) == [KIND_TSUNAMI, KIND_QUAKE]
    tsunami = _by_kind(events, KIND_TSUNAMI)
    assert tsunami.level == LEVEL_DANGER
    assert tsunami.instantaneous
    assert "M6.9" in tsunami.details
    assert tsunami.window_label(settings.timezone) == "02:00"


def test_upcoming_events_respect_lead_minutes(settings: Settings) -> None:
    """В оповещение попадают только события ближайшего часа."""
    built = hours(
        NOW,
        [
            {"shift": 1, "precipitation": 1.0, "probability": 90, "weather_code": 61},
            {"shift": 5, "wind_gust": settings.notify_hurricane_gust_kmh + 5.0},
        ],
    )
    prepared = report(settings, built, now=NOW)

    soon = upcoming_events(prepared, settings, now=NOW)
    far = upcoming_events(prepared, settings, now=NOW, lead_minutes=600)

    assert _kinds(soon) == [KIND_RAIN]
    assert KIND_HURRICANE in _kinds(far)


def test_calm_weather_has_no_events(settings: Settings) -> None:
    """Спокойные сутки не дают ни одного события."""
    built = hours(
        NOW,
        [
            {"shift": shift, "temperature": 21.0, "weather_code": 0, "probability": 5}
            for shift in range(1, 12)
        ],
    )
    events = detect_events(report(settings, built, now=NOW), settings, now=NOW)

    assert events == []


def test_events_section_is_optional(settings: Settings) -> None:
    """Раздел «События» печатается с событиями и молча пропускается без них."""
    built = hours(
        NOW,
        [
            {"shift": 1, "precipitation": 2.0, "probability": 95, "weather_code": 63},
            {"shift": 2, "weather_code": 0, "temperature": 25.0},
        ],
    )
    prepared = report(settings, built, now=NOW)
    events = detect_events(prepared, settings, now=NOW)

    text = format_report(
        prepared,
        (SECTION_EVENTS, SECTION_FORECAST),
        events=events,
        zone_name=settings.timezone,
    )
    without_events = format_report(prepared, (SECTION_EVENTS,))

    assert "События (ближайшие сверху):" in text
    assert "Дождь · 08:00–09:00" in text
    assert "сегодня, 30 сентября" in text
    assert "События" not in without_events

