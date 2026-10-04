"""Нормализованные модели данных, в которые приводятся ответы всех площадок."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from .codes import wmo_is_precipitation, wmo_kind


@dataclass(slots=True)
class CurrentWeather:
    """Текущие условия в точке по данным одной площадки."""

    source: str
    observed_at: datetime | None = None
    temperature: float | None = None
    feels_like: float | None = None
    humidity: float | None = None
    pressure: float | None = None
    wind_speed: float | None = None  # км/ч
    wind_gust: float | None = None  # км/ч
    wind_direction: float | None = None  # градусы
    cloud_cover: float | None = None  # %
    precipitation: float | None = None  # мм за интервал наблюдения
    description: str | None = None


@dataclass(slots=True)
class DayForecast:
    """Прогноз на сутки по данным одной площадки."""

    source: str
    day: date
    temp_min: float | None = None
    temp_max: float | None = None
    feels_like_max: float | None = None
    precipitation_sum: float | None = None  # мм
    precipitation_probability: float | None = None  # %
    wind_speed_max: float | None = None  # км/ч
    wind_gust_max: float | None = None  # км/ч
    uv_index_max: float | None = None
    description: str | None = None
    sunrise: str | None = None  # "06:16"
    sunset: str | None = None  # "18:03"


@dataclass(slots=True)
class MarineCurrent:
    """Текущее состояние моря по данным одной площадки."""

    source: str
    observed_at: datetime | None = None
    sea_temperature: float | None = None  # °C
    wave_height: float | None = None  # м
    wave_period: float | None = None  # с
    wave_direction: float | None = None  # градусы
    swell_height: float | None = None  # м


@dataclass(slots=True)
class MarineDay:
    """Прогноз состояния моря на сутки."""

    source: str
    day: date
    wave_height_max: float | None = None
    wave_period_max: float | None = None
    wave_direction_dominant: float | None = None
    sea_temp_min: float | None = None
    sea_temp_max: float | None = None


@dataclass(slots=True)
class HourlyPoint:
    """Почасовой прогноз одной площадки (нужен для событий с опережением)."""

    time: datetime
    source: str = ""
    temperature: float | None = None
    feels_like: float | None = None
    precipitation: float | None = None  # мм за span_hours
    precipitation_probability: float | None = None  # %
    wind_speed: float | None = None  # км/ч
    wind_gust: float | None = None  # км/ч
    wind_direction: float | None = None  # градусы
    cloud_cover: float | None = None  # %
    humidity: float | None = None  # %
    cape: float | None = None  # Дж/кг — энергия неустойчивости
    weather_code: int | None = None  # нормализованный код WMO
    description: str | None = None
    span_hours: float = 1.0  # сколько часов покрывает запись (OWM даёт шаг 3 ч)

    @property
    def precipitation_per_hour(self) -> float | None:
        """Осадки, приведённые к мм/ч."""
        if self.precipitation is None:
            return None
        span = self.span_hours if self.span_hours > 0 else 1.0
        return self.precipitation / span


@dataclass(slots=True)
class MarineHour:
    """Почасовое состояние моря (волна, период, температура воды)."""

    time: datetime
    source: str = ""
    sea_temperature: float | None = None  # °C
    wave_height: float | None = None  # м
    wave_period: float | None = None  # с
    wave_direction: float | None = None  # градусы


@dataclass(slots=True)
class Quake:
    """Землетрясение рядом с точкой прогноза (данные USGS)."""

    source: str
    time: datetime
    magnitude: float | None = None
    depth_km: float | None = None
    distance_km: float | None = None
    place: str | None = None
    url: str | None = None


@dataclass(slots=True)
class Consensus:
    """Согласованное значение поля: медиана по площадкам, минимум, максимум и разбивка."""

    median: float | None = None
    low: float | None = None
    high: float | None = None
    by_source: dict[str, float] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return self.median is not None

    @property
    def spread(self) -> float | None:
        if self.low is None or self.high is None:
            return None
        return self.high - self.low


@dataclass(slots=True)
class AggregatedHour:
    """Обобщённый почасовой прогноз по всем площадкам."""

    time: datetime
    temperature: Consensus = field(default_factory=Consensus)
    precipitation: Consensus = field(default_factory=Consensus)  # мм/ч
    precipitation_probability: Consensus = field(default_factory=Consensus)
    wind_speed: Consensus = field(default_factory=Consensus)
    wind_gust: Consensus = field(default_factory=Consensus)
    cloud_cover: Consensus = field(default_factory=Consensus)
    humidity: Consensus = field(default_factory=Consensus)
    cape: Consensus = field(default_factory=Consensus)
    weather_code: int | None = None
    description: str | None = None
    descriptions: dict[str, str] = field(default_factory=dict)
    sources: tuple[str, ...] = ()

    @property
    def wet(self) -> bool:
        """Ожидаются ли в этот час осадки по консенсусу площадок."""
        amount = self.precipitation.median or 0.0
        chance = self.precipitation_probability.median or 0.0
        return amount >= 0.1 or chance >= 55 or wmo_is_precipitation(self.weather_code)

    @property
    def kind(self) -> str:
        """Тип ожидаемых осадков: snow, thunder, rain, fog или none."""
        kind = wmo_kind(self.weather_code)
        if kind != "none":
            return kind
        if not self.wet:
            return "none"
        temperature = self.temperature.median
        if temperature is None:
            return "rain"
        if temperature <= 0.5:
            return "snow"
        if temperature <= 2.5:
            return "sleet"
        return "rain"


@dataclass(slots=True)
class AggregatedMarineHour:
    """Обобщённое почасовое состояние моря."""

    time: datetime
    wave_height: Consensus = field(default_factory=Consensus)
    wave_period: Consensus = field(default_factory=Consensus)
    sea_temperature: Consensus = field(default_factory=Consensus)


@dataclass(slots=True)
class SourceResult:
    """Результат опроса одной площадки: данные либо текст ошибки."""

    source: str
    current: CurrentWeather | None = None
    days: list[DayForecast] = field(default_factory=list)
    hourly: list[HourlyPoint] = field(default_factory=list)
    marine_current: MarineCurrent | None = None
    marine_days: list[MarineDay] = field(default_factory=list)
    marine_hours: list[MarineHour] = field(default_factory=list)
    quakes: list[Quake] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def has_data(self) -> bool:
        return any(
            (
                self.current is not None,
                bool(self.days),
                self.marine_current is not None,
                bool(self.marine_days),
                bool(self.hourly),
                bool(self.quakes),
            )
        )


@dataclass(slots=True)
class AggregatedCurrent:
    """Обобщённые текущие условия по всем площадкам."""

    observed_at: datetime | None = None
    description: str | None = None
    descriptions: dict[str, str] = field(default_factory=dict)
    temperature: Consensus = field(default_factory=Consensus)
    feels_like: Consensus = field(default_factory=Consensus)
    humidity: Consensus = field(default_factory=Consensus)
    pressure: Consensus = field(default_factory=Consensus)
    wind_speed: Consensus = field(default_factory=Consensus)
    wind_gust: Consensus = field(default_factory=Consensus)
    wind_direction: Consensus = field(default_factory=Consensus)
    cloud_cover: Consensus = field(default_factory=Consensus)
    precipitation: Consensus = field(default_factory=Consensus)


@dataclass(slots=True)
class AggregatedDay:
    """Обобщённый прогноз на сутки."""

    day: date
    description: str | None = None
    descriptions: dict[str, str] = field(default_factory=dict)
    temp_min: Consensus = field(default_factory=Consensus)
    temp_max: Consensus = field(default_factory=Consensus)
    feels_like_max: Consensus = field(default_factory=Consensus)
    precipitation_sum: Consensus = field(default_factory=Consensus)
    precipitation_probability: Consensus = field(default_factory=Consensus)
    wind_speed_max: Consensus = field(default_factory=Consensus)
    wind_gust_max: Consensus = field(default_factory=Consensus)
    uv_index_max: Consensus = field(default_factory=Consensus)
    sunrise: str | None = None
    sunset: str | None = None


@dataclass(slots=True)
class AggregatedMarine:
    """Обобщённое текущее состояние моря."""

    observed_at: datetime | None = None
    sea_temperature: Consensus = field(default_factory=Consensus)
    wave_height: Consensus = field(default_factory=Consensus)
    wave_period: Consensus = field(default_factory=Consensus)
    wave_direction: Consensus = field(default_factory=Consensus)
    swell_height: Consensus = field(default_factory=Consensus)


@dataclass(slots=True)
class AggregatedMarineDay:
    """Обобщённый прогноз состояния моря на сутки."""

    day: date
    wave_height_max: Consensus = field(default_factory=Consensus)
    wave_period_max: Consensus = field(default_factory=Consensus)
    wave_direction_dominant: Consensus = field(default_factory=Consensus)
    sea_temp_min: Consensus = field(default_factory=Consensus)
    sea_temp_max: Consensus = field(default_factory=Consensus)


@dataclass(slots=True)
class Report:
    """Итоговая обобщённая сводка, готовая к форматированию в текст."""

    city: str
    lat: float
    lon: float
    generated_at: datetime
    timezone_label: str
    sources_ok: list[str] = field(default_factory=list)
    sources_failed: list[tuple[str, str]] = field(default_factory=list)
    sources_skipped: list[str] = field(default_factory=list)
    current: AggregatedCurrent | None = None
    days: list[AggregatedDay] = field(default_factory=list)
    hours: list[AggregatedHour] = field(default_factory=list)
    marine: AggregatedMarine | None = None
    marine_days: list[AggregatedMarineDay] = field(default_factory=list)
    marine_hours: list[AggregatedMarineHour] = field(default_factory=list)
    quakes: list[Quake] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    raw_results: list[SourceResult] = field(default_factory=list)
