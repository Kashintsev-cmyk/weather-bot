"""Площадка USGS: землетрясения рядом с точкой прогноза (без ключа).

Погоду не отдаёт, но нужна детектору событий: сильные толчки в регионе —
повод предупредить пользователя (и проверить официальные предупреждения).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import httpx

from ..config import Settings
from ..models import Quake, SourceResult
from .base import SourceError, WeatherSource, to_float

API_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
NAME = "USGS"
MAX_QUAKES = 20
EARTH_RADIUS_KM = 6371.0


class UsgsQuakesSource(WeatherSource):
    """Лента землетрясений USGS в радиусе от точки прогноза."""

    name = NAME

    @property
    def enabled(self) -> bool:
        return self.settings.earthquakes_enabled

    @property
    def disabled_reason(self) -> str | None:
        if self.settings.earthquakes_enabled:
            return None
        return "оповещения о землетрясениях отключены (EARTHQUAKES_ENABLED=0)"

    async def collect(self, client: httpx.AsyncClient) -> SourceResult:
        payload = await self._get_json(client, API_URL, params=self.build_params(self.settings))
        try:
            return parse(payload, self.settings)
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceError(f"неожиданный формат ответа: {exc!r}") from exc

    @staticmethod
    def build_params(settings: Settings) -> dict[str, object]:
        """Параметры запроса FDSN: свежие толчки выше порога в радиусе точки."""
        start = datetime.now(tz=timezone.utc) - timedelta(days=settings.earthquake_days)
        return {
            "format": "geojson",
            "latitude": settings.lat,
            "longitude": settings.lon,
            "maxradiuskm": settings.earthquake_radius_km,
            "minmagnitude": settings.earthquake_min_magnitude,
            "limit": MAX_QUAKES,
            "orderby": "time",
            "starttime": start.strftime("%Y-%m-%dT%H:%M:%S"),
        }


def parse(payload: dict, settings: Settings) -> SourceResult:
    """Нормализует GeoJSON-ответ USGS в список Quake."""
    features = payload.get("features") or []
    quakes: list[Quake] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        quake = _parse_feature(feature, settings)
        if quake is not None:
            quakes.append(quake)
    return SourceResult(source=NAME, quakes=quakes)


def _parse_feature(feature: dict, settings: Settings) -> Quake | None:
    properties = feature.get("properties") or {}
    geometry = feature.get("geometry") or {}
    moment = _moment(properties.get("time"))
    if moment is None:
        return None

    coordinates = geometry.get("coordinates") or []
    depth = to_float(coordinates[2]) if len(coordinates) > 2 else None
    lat = to_float(coordinates[1]) if len(coordinates) > 1 else None
    lon = to_float(coordinates[0]) if len(coordinates) > 0 else None

    distance = None
    if lat is not None and lon is not None:
        distance = round(haversine_km(settings.lat, settings.lon, lat, lon), 1)

    return Quake(
        source=NAME,
        time=moment,
        magnitude=to_float(properties.get("mag")),
        depth_km=depth,
        distance_km=distance,
        place=_place(properties.get("place")),
        url=properties.get("url") if isinstance(properties.get("url"), str) else None,
    )


def _moment(value: object) -> datetime | None:
    """Время события: epoch в миллисекундах -> datetime в UTC."""
    milliseconds = to_float(value)
    if milliseconds is None:
        return None
    return datetime.fromtimestamp(milliseconds / 1000.0, tz=timezone.utc)


def _place(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние между двумя точками по большому кругу, км."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    inner = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(inner)))
