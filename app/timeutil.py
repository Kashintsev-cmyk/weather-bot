"""Работа с часовыми поясами и локальным временем точки прогноза."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

# Резервная зона, если база tzdata в системе недоступна.
FALLBACK_ZONE: tzinfo = timezone(timedelta(hours=3), name="UTC+3")


def safe_zone(zone_name: str) -> tzinfo:
    """Возвращает tzinfo по имени зоны; при ошибке — резервную зону UTC+3."""
    try:
        return ZoneInfo(zone_name)
    except Exception:  # ZoneInfoNotFoundError и любые проблемы tzdata
        return FALLBACK_ZONE


def local_now(zone_name: str) -> datetime:
    """Текущее время в часовом поясе точки прогноза."""
    return datetime.now(safe_zone(zone_name))


def as_local(moment: datetime | None, zone_name: str) -> datetime | None:
    """Приводит момент времени к часовому поясу точки прогноза."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=safe_zone(zone_name))
    return moment.astimezone(safe_zone(zone_name))
