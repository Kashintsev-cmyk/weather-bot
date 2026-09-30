"""Состояние бота: подписчики на рассылку и уже отправленные уведомления.

Данные лежат в JSON-файле (STATE_FILE в app/config.py), чтобы бота можно было
перезапускать и обновлять без повторной отправки одних и тех же оповещений.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

STATE_VERSION = 1
# Сколько дней храним историю отправленного (файл не растёт бесконечно).
KEEP_DAYS = 7


@dataclass(slots=True)
class BotState:
    """Содержимое файла состояния."""

    subscribers: list[int] = field(default_factory=list)
    # дата (ГГГГ-ММ-ДД) -> время отправки ежедневной сводки
    daily: dict[str, str] = field(default_factory=dict)
    # дата -> ключи уже отправленных событий
    alerts: dict[str, list[str]] = field(default_factory=dict)


class StateStore:
    """Чтение и атомарная запись состояния бота."""

    def __init__(self, path: Path, default_subscribers: Iterable[int] = ()) -> None:
        self.path = Path(path)
        self.default_subscribers = [int(item) for item in default_subscribers]
        self._state: BotState | None = None

    @property
    def state(self) -> BotState:
        if self._state is None:
            self._state = self.load()
        return self._state

    def load(self) -> BotState:
        """Читает файл состояния; при отсутствии или ошибке — состояние по умолчанию."""
        if not self.path.exists():
            return BotState(subscribers=list(self.default_subscribers))

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("Не удалось прочитать %s: %s", self.path, exc)
            return BotState(subscribers=list(self.default_subscribers))

        if not isinstance(raw, dict):
            return BotState(subscribers=list(self.default_subscribers))

        subscribers = [
            int(item)
            for item in (raw.get("subscribers") or [])
            if isinstance(item, (int, str)) and str(item).lstrip("-").isdigit()
        ]
        if not subscribers:
            subscribers = list(self.default_subscribers)

        daily = {str(day): str(moment) for day, moment in (raw.get("daily") or {}).items()}
        alerts = {
            str(day): [str(key) for key in keys]
            for day, keys in (raw.get("alerts") or {}).items()
            if isinstance(keys, list)
        }
        return BotState(subscribers=subscribers, daily=daily, alerts=alerts)

    def save(self) -> None:
        """Пишет состояние атомарно (через временный файл)."""
        state = self.state
        state.daily = _prune(state.daily)
        state.alerts = _prune(state.alerts)
        payload = {
            "version": STATE_VERSION,
            "subscribers": state.subscribers,
            "daily": state.daily,
            "alerts": state.alerts,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self.path)

    # ------------------------------------------------------------------
    # Подписки
    # ------------------------------------------------------------------

    def subscribers(self) -> tuple[int, ...]:
        return tuple(sorted(set(self.state.subscribers)))

    def is_subscribed(self, chat_id: int) -> bool:
        return int(chat_id) in set(self.state.subscribers)

    def subscribe(self, chat_id: int) -> bool:
        """Добавляет chat_id; False, если он уже был подписан."""
        chat_id = int(chat_id)
        if chat_id in self.state.subscribers:
            return False
        self.state.subscribers.append(chat_id)
        self.save()
        return True

    def unsubscribe(self, chat_id: int) -> bool:
        """Убирает chat_id; False, если он не был подписан."""
        chat_id = int(chat_id)
        if chat_id not in self.state.subscribers:
            return False
        self.state.subscribers = [
            item for item in self.state.subscribers if item != chat_id
        ]
        self.save()
        return True

    # ------------------------------------------------------------------
    # Дедупликация отправленного
    # ------------------------------------------------------------------

    def daily_sent(self, day: date) -> bool:
        return day.isoformat() in self.state.daily

    def mark_daily(self, day: date, moment: datetime | None = None) -> None:
        stamp = (moment or datetime.now()).isoformat(timespec="seconds")
        self.state.daily[day.isoformat()] = stamp
        self.save()

    def alert_sent(self, key: str) -> bool:
        """Отправляли ли уже событие с таким ключом."""
        return any(key in keys for keys in self.state.alerts.values())

    def mark_alert(self, key: str, day: date) -> None:
        keys = self.state.alerts.setdefault(day.isoformat(), [])
        if key not in keys:
            keys.append(key)
        self.save()

    def forget_old(self, today: date | None = None) -> None:
        """Забывает историю старше KEEP_DAYS (полезно при разборе проблем)."""
        moment = today or date.today()
        self.state.daily = _prune(self.state.daily, moment)
        self.state.alerts = _prune(self.state.alerts, moment)
        self.save()


def _prune(values: dict, today: date | None = None) -> dict:
    """Оставляет только записи за последние KEEP_DAYS суток."""
    limit = (today or date.today()) - timedelta(days=KEEP_DAYS)
    kept = {}
    for key, value in values.items():
        try:
            day = date.fromisoformat(key)
        except ValueError:
            continue
        if day >= limit:
            kept[key] = value
    return kept