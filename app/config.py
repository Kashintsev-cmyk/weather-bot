"""Конфигурация приложения: точка прогноза, ключи площадок и параметры запросов."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"

logger = logging.getLogger(__name__)

# Значения по умолчанию соответствуют точке в Адлере (Сочи).
DEFAULT_CITY = "Адлер"
DEFAULT_LAT = 43.427292
DEFAULT_LON = 39.917107
DEFAULT_TIMEZONE = "Europe/Moscow"
# Файл состояния: подписки на рассылку и уже отправленные уведомления.
DEFAULT_STATE_FILE = BASE_DIR / "state" / "bot_state.json"


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        logger.warning("%s=%r — не число, использую default %s", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("%s=%r — не целое, использую default %s", name, raw, default)
        return default


def _env_id_tuple(name: str) -> tuple[int, ...]:
    raw = os.getenv(name, "")
    ids: list[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            ids.append(int(chunk))
        except ValueError:
            logger.warning("%s: пропущен невалидный id %r", name, chunk)
            continue
    return tuple(ids)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "да"}


def _env_clock(name: str, default: str) -> str:
    """Время вида 'HH:MM' (например 07:30); при ошибке — значение по умолчанию."""
    hours_text, _, minutes_text = _env_str(name, default).partition(":")
    try:
        hours, minutes = int(hours_text), int(minutes_text[:2])
    except ValueError:
        return default
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return default
    return f"{hours:02d}:{minutes:02d}"


@dataclass(frozen=True, slots=True)
class Settings:
    """Настройки запуска, собираемые из переменных окружения (.env)."""

    city: str = DEFAULT_CITY
    lat: float = DEFAULT_LAT
    lon: float = DEFAULT_LON
    timezone: str = DEFAULT_TIMEZONE
    forecast_days: int = 3
    request_timeout: float = 15.0
    # Отладочный режим: подробное логирование и диагностические детали.
    debug_mode: bool = False
    openweather_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_allowed_chat_ids: tuple[int, ...] = field(default_factory=tuple)
    # Рассылка: ежедневная сводка и оповещения о событиях.
    notify_events_enabled: bool = True
    notify_daily_time: str = "07:30"
    notify_event_lead_minutes: int = 60
    notify_event_horizon_hours: int = 12
    notify_poll_seconds: int = 60
    notify_daily_grace_minutes: int = 180
    notify_rain_mm_per_hour: float = 0.2
    notify_rain_probability: int = 55
    notify_gust_kmh: float = 55.0
    notify_hurricane_gust_kmh: float = 90.0
    notify_wave_m: float = 1.5
    notify_wave_severe_m: float = 2.5
    notify_temp_drop_c: float = 7.0
    notify_heat_c: float = 33.0
    notify_tornado_cape: float = 1200.0
    # Землетрясения (USGS).
    earthquakes_enabled: bool = True
    earthquake_radius_km: float = 300.0
    earthquake_min_magnitude: float = 3.0
    earthquake_days: int = 7
    tsunami_magnitude: float = 6.5
    # Состояние бота: подписки и отправленные уведомления.
    state_file: Path = DEFAULT_STATE_FILE

    @property
    def coords_label(self) -> str:
        return f"{self.lat:.6f}, {self.lon:.6f}"

    @property
    def notify_daily_minutes(self) -> int:
        """Время ежедневной сводки в минутах от начала суток."""
        hours, minutes = self.notify_daily_time.split(":")
        return int(hours) * 60 + int(minutes)

    @property
    def timezone_label(self) -> str:
        """Короткая подпись часового пояса для текста сводки."""
        return {"Europe/Moscow": "МСК", "Europe/Kaliningrad": "КАЛ", "UTC": "UTC"}.get(
            self.timezone, self.timezone
        )

    @property
    def has_openweather(self) -> bool:
        return bool(self.openweather_api_key)

    @classmethod
    def from_env(cls, env_file: Path | None = ENV_FILE) -> Settings:
        """Читает настройки из окружения, при наличии — из файла .env."""
        if env_file is not None and Path(env_file).exists():
            load_dotenv(env_file, override=False)

        api_key = _env_str("OPENWEATHER_API_KEY", "")
        return cls(
            city=_env_str("WEATHER_CITY", DEFAULT_CITY),
            lat=_env_float("WEATHER_LAT", DEFAULT_LAT),
            lon=_env_float("WEATHER_LON", DEFAULT_LON),
            timezone=_env_str("WEATHER_TIMEZONE", DEFAULT_TIMEZONE),
            forecast_days=max(1, min(7, _env_int("FORECAST_DAYS", 3))),
            request_timeout=max(3.0, _env_float("REQUEST_TIMEOUT", 15.0)),
            debug_mode=_env_bool("DEBUG_MODE", False),
            openweather_api_key=api_key,
            telegram_bot_token=_env_str("TELEGRAM_BOT_TOKEN", ""),
            telegram_allowed_chat_ids=_env_id_tuple("TELEGRAM_ALLOWED_CHAT_IDS"),
            notify_events_enabled=_env_bool("NOTIFY_EVENTS_ENABLED", True),
            notify_daily_time=_env_clock("NOTIFY_DAILY_TIME", "07:30"),
            notify_event_lead_minutes=max(0, _env_int("NOTIFY_EVENT_LEAD_MINUTES", 60)),
            notify_event_horizon_hours=max(1, _env_int("NOTIFY_EVENT_HORIZON_HOURS", 12)),
            notify_poll_seconds=max(15, _env_int("NOTIFY_POLL_SECONDS", 60)),
            notify_daily_grace_minutes=max(
                0, _env_int("NOTIFY_DAILY_GRACE_MINUTES", 180)
            ),
            notify_rain_mm_per_hour=max(0.0, _env_float("NOTIFY_RAIN_MM_PER_HOUR", 0.2)),
            notify_rain_probability=max(0, min(100, _env_int("NOTIFY_RAIN_PROBABILITY", 55))),
            notify_gust_kmh=_env_float("NOTIFY_GUST_KMH", 55.0),
            notify_hurricane_gust_kmh=_env_float("NOTIFY_HURRICANE_GUST_KMH", 90.0),
            notify_wave_m=_env_float("NOTIFY_WAVE_M", 1.5),
            notify_wave_severe_m=_env_float("NOTIFY_WAVE_SEVERE_M", 2.5),
            notify_temp_drop_c=_env_float("NOTIFY_TEMP_DROP_C", 7.0),
            notify_heat_c=_env_float("NOTIFY_HEAT_C", 33.0),
            notify_tornado_cape=_env_float("NOTIFY_TORNADO_CAPE", 1200.0),
            earthquakes_enabled=_env_bool("EARTHQUAKES_ENABLED", True),
            earthquake_radius_km=_env_float("EARTHQUAKE_RADIUS_KM", 300.0),
            earthquake_min_magnitude=_env_float("EARTHQUAKE_MIN_MAGNITUDE", 3.0),
            earthquake_days=max(1, min(30, _env_int("EARTHQUAKE_DAYS", 14))),
            tsunami_magnitude=_env_float("TSUNAMI_MAGNITUDE", 6.5),
            state_file=Path(_env_str("STATE_FILE", str(DEFAULT_STATE_FILE))).expanduser(),
        )

    def is_allowed_chat(self, chat_id: int) -> bool:
        """Проверяет chat_id по необязательному белому списку."""
        if not self.telegram_allowed_chat_ids:
            return True
        return chat_id in self.telegram_allowed_chat_ids
