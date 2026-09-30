"""Базовые абстракции площадок-источников и общие помощники разбора ответов."""

from __future__ import annotations

from datetime import date, datetime

import httpx

from ..config import Settings
from ..models import SourceResult
from ..timeutil import safe_zone

USER_AGENT = "sochi-weather-bot/1.0 (https://github.com/) httpx"


class SourceError(RuntimeError):
    """Площадка недоступна или вернула неожиданный формат данных."""


class WeatherSource:
    """Площадка прогноза: умеет опрашивать API и нормализовать ответ."""

    name = "base"
    requires_key = False

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        """Площадка участвует в опросе (например, есть ли API-ключ)."""
        return True

    @property
    def disabled_reason(self) -> str | None:
        return None

    async def collect(self, client: httpx.AsyncClient) -> SourceResult:
        raise NotImplementedError

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        url: str,
        params: dict[str, object] | None = None,
    ) -> dict:
        """Выполняет GET-запрос и возвращает JSON, приводя ошибки к SourceError."""
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise SourceError(f"HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise SourceError(f"сеть недоступна ({exc.__class__.__name__})") from exc
        except ValueError as exc:
            raise SourceError("ответ не является JSON") from exc
        if not isinstance(payload, dict):
            raise SourceError("неожиданный тип JSON-ответа")
        return payload


# ---------------------------------------------------------------------------
# Помощники разбора, используемые площадками
# ---------------------------------------------------------------------------


def to_float(value: object) -> float | None:
    """Приводит значение к float, возвращая None для пустых значений."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", ".")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def pick(values: object, index: int) -> object:
    """Безопасно берёт элемент списка по индексу."""
    if isinstance(values, list) and 0 <= index < len(values):
        return values[index]
    return None


def parse_local_dt(value: object, zone_name: str) -> datetime | None:
    """Разбирает ISO-строку локального времени источника."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=safe_zone(zone_name))
    return moment


def parse_date(value: object) -> date | None:
    """Разбирает дату в формате ISO (YYYY-MM-DD)."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def time_only(value: object) -> str | None:
    """Оставляет от ISO-строки только часы и минуты: '2026-09-29T06:16' -> '06:16'."""
    if not isinstance(value, str) or "T" not in value:
        return None
    _, _, clock = value.partition("T")
    clock = clock[:5]
    return clock if len(clock) == 5 else None
