"""Формирование текста обобщённой сводки (стиль — как в hello.txt)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime

from .aggregate import TEMP_SPREAD_WARNING
from .config import DEFAULT_TIMEZONE
from .events import Event
from .models import (
    AggregatedCurrent,
    AggregatedDay,
    AggregatedMarine,
    AggregatedMarineDay,
    Report,
)
from .timeutil import as_local

MONTHS_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
WEEKDAYS = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)
COMPASS_POINTS = (
    "С",
    "ССВ",
    "СВ",
    "ВСВ",
    "В",
    "ВЮВ",
    "ЮВ",
    "ЮЮВ",
    "Ю",
    "ЮЮЗ",
    "ЮЗ",
    "ЗЮЗ",
    "З",
    "ЗСЗ",
    "СЗ",
    "ССЗ",
)

SECTION_CURRENT = "current"
SECTION_EVENTS = "events"
SECTION_FORECAST = "forecast"
SECTION_MARINE = "marine"
SECTION_SEA_FORECAST = "sea_forecast"
SECTION_SOURCES = "sources"
SECTION_NOTES = "notes"

ALL_SECTIONS = (
    SECTION_CURRENT,
    SECTION_MARINE,
    SECTION_EVENTS,
    SECTION_FORECAST,
    SECTION_SEA_FORECAST,
    SECTION_SOURCES,
    SECTION_NOTES,
)


def format_report(
    report: Report,
    sections: Iterable[str] = ALL_SECTIONS,
    events: Iterable[Event] | None = None,
    zone_name: str | None = None,
) -> str:
    """Собирает полный текст сводки, включая только запрошенные разделы.

    Раздел «События» печатается, только если переданы события: без настроек
    детектор не знает порогов, поэтому молча пропускаем раздел.
    """
    wanted = set(sections)
    blocks: list[str] = [_header(report)]

    if SECTION_CURRENT in wanted:
        if report.current is not None:
            blocks.append(_current_block(report.current))
        else:
            blocks.append("Текущие условия: площадки не отдали данные.")

    if SECTION_MARINE in wanted and report.marine is not None:
        blocks.append(_marine_block(report.marine))

    if SECTION_EVENTS in wanted and events is not None:
        blocks.append(
            _events_block(
                list(events),
                zone_name or DEFAULT_TIMEZONE,
                report.generated_at.date(),
            )
        )

    if SECTION_FORECAST in wanted:
        if report.days:
            blocks.append(_days_block(report.days, report.generated_at.date()))
        else:
            blocks.append("Прогноз по дням: данных нет.")

    if SECTION_SEA_FORECAST in wanted and report.marine_days:
        blocks.append(_marine_days_block(report.marine_days, report.generated_at.date()))

    if SECTION_SOURCES in wanted:
        blocks.append(_sources_block(report))

    if SECTION_NOTES in wanted and report.notes:
        blocks.append(_notes_block(report.notes))

    return "\n\n".join(block for block in blocks if block.strip())


# ---------------------------------------------------------------------------
# Разделы сводки
# ---------------------------------------------------------------------------


def _header(report: Report) -> str:
    total = (
        len(report.sources_ok) + len(report.sources_failed) + len(report.sources_skipped)
    )
    lines = [
        f"{report.city} — обобщённая сводка погоды и моря",
        f"Обновлено: {_date_long(report.generated_at.date())}, "
        f"{_clock(report.generated_at)} ({report.timezone_label}). "
        f"Точка: {report.lat:.6f}, {report.lon:.6f}",
    ]
    if report.sources_ok:
        lines.append(
            f"Источники: {', '.join(report.sources_ok)} "
            f"({len(report.sources_ok)} из {total})"
        )
    elif total:
        lines.append(f"Источники: ни одна из {total} площадок не ответила")
    else:
        lines.append("Источники: ни одна площадка не была опрошена")
    return "\n".join(lines)


def _current_block(current: AggregatedCurrent) -> str:
    lines: list[str] = []

    headline: list[str] = []
    if current.temperature.available:
        headline.append(_temp(current.temperature.median))
    if current.feels_like.available:
        headline.append(f"ощущается как {_temp(current.feels_like.median)}")
    description = current.description or "описание не получено"
    lines.append(f"Сейчас: {', '.join(headline) or 'нет данных о температуре'}, {description}.")

    if current.temperature.spread:
        lines.append(
            f"Площадки дают {_temp_range(current.temperature.low, current.temperature.high)} "
            f"(медиана {_temp(current.temperature.median)})."
        )

    wind_line: list[str] = []
    if current.humidity.available:
        wind_line.append(f"влажность {_num(current.humidity.median)}%")
    if current.wind_speed.available:
        wind = f"ветер {_num(current.wind_speed.median)} км/ч"
        if current.wind_direction.available:
            direction = _num(current.wind_direction.median)
            wind += f" ({_compass(current.wind_direction.median)}, {direction}°)"
        wind_line.append(wind)
    if current.wind_gust.available:
        wind_line.append(f"порывы до {_num(current.wind_gust.median)} км/ч")
    if wind_line:
        lines.append(_sentence(wind_line))

    extra: list[str] = []
    if current.pressure.available:
        extra.append(f"давление {_num(current.pressure.median)} гПа")
    if current.cloud_cover.available:
        extra.append(f"облачность {_num(current.cloud_cover.median)}%")
    if current.precipitation.available:
        extra.append(
            f"осадки {_num(current.precipitation.median, 1)} мм за интервал наблюдения"
        )
    if extra:
        lines.append(_sentence(extra))

    return "\n".join(lines)


def _marine_block(marine: AggregatedMarine) -> str:
    lines: list[str] = []

    headline: list[str] = []
    if marine.sea_temperature.available:
        headline.append(f"вода {_temp(marine.sea_temperature.median)}")
    if marine.wave_height.available:
        headline.append(f"волна {_num(marine.wave_height.median, 1)} м")
    lines.append(f"Море: {', '.join(headline) or 'нет данных о состоянии моря'}.")

    details: list[str] = []
    if marine.wave_period.available:
        details.append(f"период волны {_num(marine.wave_period.median, 1)} с")
    if marine.wave_direction.available:
        details.append(
            f"направление {_compass(marine.wave_direction.median)} "
            f"({_num(marine.wave_direction.median)}°)"
        )
    if marine.swell_height.available:
        details.append(f"зыбь {_num(marine.swell_height.median, 1)} м")
    if details:
        lines.append(_sentence(details))

    return "\n".join(lines)


def _days_block(days: list[AggregatedDay], today: date) -> str:
    lines = ["Прогноз по дням (обобщённо):"]
    for day in days:
        lines.append("")
        parts: list[str] = []
        if day.temp_min.available or day.temp_max.available:
            parts.append(_temp_range(day.temp_min.median, day.temp_max.median))
        if day.description:
            parts.append(day.description)
        lines.append(f"• {_day_label(day.day, today)} — {', '.join(parts) or 'нет данных'}.")

        details: list[str] = []
        if day.feels_like_max.available:
            details.append(f"ощущается до {_temp(day.feels_like_max.median)}")
        if day.precipitation_sum.available:
            precipitation = f"осадки до {_num(day.precipitation_sum.median, 1)} мм"
            if day.precipitation_probability.available:
                precipitation += (
                    f" (вероятность {_num(day.precipitation_probability.median)}%)"
                )
            details.append(precipitation)
        if day.wind_speed_max.available:
            details.append(f"ветер до {_num(day.wind_speed_max.median)} км/ч")
        if day.wind_gust_max.available:
            details.append(f"порывы до {_num(day.wind_gust_max.median)} км/ч")
        if day.uv_index_max.available:
            details.append(f"УФ-индекс до {_num(day.uv_index_max.median, 1)}")
        if details:
            lines.append(f"  {_sentence(details)}")

        astro: list[str] = []
        if day.sunrise:
            astro.append(f"рассвет {day.sunrise}")
        if day.sunset:
            astro.append(f"закат {day.sunset}")
        if astro:
            lines.append(f"  {', '.join(astro)}.")

        if len(day.descriptions) > 1:
            by_source = "; ".join(
                f"{source} — {text}" for source, text in day.descriptions.items()
            )
            lines.append(f"  Описания по площадкам: {by_source}.")

        if _day_temp_spread(day) >= TEMP_SPREAD_WARNING:
            spans: list[str] = []
            if day.temp_min.available:
                spans.append(
                    f"минимум {_temp_range(day.temp_min.low, day.temp_min.high)}"
                )
            if day.temp_max.available:
                spans.append(
                    f"максимум {_temp_range(day.temp_max.low, day.temp_max.high)}"
                )
            if spans:
                lines.append(
                    f"  Расхождение площадок: {', '.join(spans)}."
                )
    return "\n".join(lines)


def _marine_days_block(days: list[AggregatedMarineDay], today: date) -> str:
    lines = ["Море по дням:"]
    for day in days:
        parts: list[str] = []
        if day.wave_height_max.available:
            parts.append(f"волна до {_num(day.wave_height_max.median, 1)} м")
        if day.wave_period_max.available:
            parts.append(f"период до {_num(day.wave_period_max.median, 1)} с")
        if day.wave_direction_dominant.available:
            parts.append(
                f"направление {_compass(day.wave_direction_dominant.median)} "
                f"({_num(day.wave_direction_dominant.median)}°)"
            )
        if day.sea_temp_min.available or day.sea_temp_max.available:
            parts.append(
                f"вода {_temp_range(day.sea_temp_min.median, day.sea_temp_max.median)}"
            )
        lines.append(f"• {_day_label(day.day, today)}: {', '.join(parts) or 'нет данных'}.")
    return "\n".join(lines)


def _events_block(events: list[Event], zone_name: str, today: date) -> str:
    """Раздел «События»: группируем по дням, чтобы был виден горизонт."""
    if not events:
        return "События: опасных явлений в ближайшем прогнозе не ожидается."

    lines = ["События (ближайшие сверху):"]
    current_day: date | None = None
    for event in events:
        day = as_local(event.starts_at, zone_name).date()
        if day != current_day:
            lines.append("")
            lines.append(f"— {_day_label(day, today)}:")
            current_day = day
        lines.extend(f"  {line}" for line in event.headline(zone_name).splitlines())
    return "\n".join(lines)


def format_events(events: Iterable[Event], zone_name: str = DEFAULT_TIMEZONE) -> str:
    """Короткий список событий: подходит для команды /events и оповещений."""
    lines = [
        line
        for event in events
        for line in event.headline(zone_name).splitlines()
    ]
    if not lines:
        return "Событий в ближайшем прогнозе нет."
    return "\n".join(lines)


def _sources_block(report: Report) -> str:
    lines = ["Площадки и разброс данных:"]
    for result in report.raw_results:
        if not result.ok or not result.has_data:
            continue
        details: list[str] = []
        if result.current is not None:
            details.append(_temp(result.current.temperature))
            if result.current.description:
                details.append(result.current.description)
            if result.current.observed_at:
                details.append(f"наблюдение {_clock(result.current.observed_at)}")
        if result.marine_current is not None:
            sea = result.marine_current
            if sea.sea_temperature is not None:
                details.append(f"вода {_temp(sea.sea_temperature)}")
            if sea.wave_height is not None:
                details.append(f"волна {_num(sea.wave_height, 1)} м")
        lines.append(f"— {result.source}: {', '.join(details) or 'без числовых полей'}")

    spread = _spread_line(report)
    if spread:
        lines.append(spread)
    return "\n".join(lines)


def _notes_block(notes: list[str]) -> str:
    lines = ["Примечания:"]
    lines.extend(f"— {note}" for note in notes)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Помощники форматирования
# ---------------------------------------------------------------------------


def _spread_line(report: Report) -> str | None:
    """Строка о разбросе ключевых значений между площадками."""
    current = report.current
    if current is None:
        return None
    parts: list[str] = []
    if current.temperature.spread:
        parts.append(
            f"температура {_temp_range(current.temperature.low, current.temperature.high)}"
        )
    if current.wind_speed.spread:
        parts.append(
            f"ветер {_num(current.wind_speed.low)}…{_num(current.wind_speed.high)} км/ч"
        )
    if current.humidity.spread:
        parts.append(
            f"влажность {_num(current.humidity.low)}…{_num(current.humidity.high)}%"
        )
    if current.pressure.spread:
        parts.append(
            f"давление {_num(current.pressure.low)}…{_num(current.pressure.high)} гПа"
        )
    if not parts:
        return None
    return "Разброс по источникам: " + ", ".join(parts) + "."


def _day_temp_spread(day: AggregatedDay) -> float:
    """Максимальный разброс температур по площадкам за сутки."""
    spreads = [
        spread
        for spread in (day.temp_min.spread, day.temp_max.spread)
        if spread is not None
    ]
    return max(spreads) if spreads else 0.0


def _sentence(parts: list[str]) -> str:
    """Склеивает фрагменты в предложение с заглавной буквы и точкой."""
    text = "; ".join(part for part in parts if part)
    if not text:
        return ""
    return text[0].upper() + text[1:] + "."


def _temp(value: float | None, digits: int = 0) -> str:
    """Температура со знаком: '+19 °C'."""
    if value is None:
        return "н/д"
    return f"{value:+.{digits}f} °C"


def _temp_range(low: float | None, high: float | None) -> str:
    """Диапазон температур: '+16…+21 °C'."""
    if low is None and high is None:
        return "н/д"
    if low is None:
        return f"до {_temp(high)}"
    if high is None:
        return f"{_temp(low)} и выше"
    if round(low) == round(high):
        return _temp(high)
    return f"{low:+.0f}…{high:+.0f} °C"


def _num(value: float | None, digits: int = 0) -> str:
    """Число без знака плюс: '7', '4.1'."""
    if value is None:
        return "н/д"
    return f"{value:.{digits}f}"


def _compass(degrees: float | None) -> str:
    """Румб на русском: 122° -> 'ВЮВ'."""
    if degrees is None:
        return "н/д"
    index = int((degrees % 360) / 22.5 + 0.5) % 16
    return COMPASS_POINTS[index]


def _date_long(day: date) -> str:
    """'29 сентября 2026'."""
    return f"{day.day} {MONTHS_GENITIVE[day.month - 1]} {day.year}"


def _day_label(day: date, today: date) -> str:
    """'сегодня, 29 сентября', 'завтра, 30 сентября' или 'среда, 1 октября'."""
    delta = (day - today).days
    if delta == 0:
        return f"сегодня, {day.day} {MONTHS_GENITIVE[day.month - 1]}"
    if delta == 1:
        return f"завтра, {day.day} {MONTHS_GENITIVE[day.month - 1]}"
    return f"{WEEKDAYS[day.weekday()]}, {day.day} {MONTHS_GENITIVE[day.month - 1]}"


def _clock(moment: datetime | None) -> str:
    """'23:41' из datetime."""
    if moment is None:
        return "н/д"
    return moment.strftime("%H:%M")


