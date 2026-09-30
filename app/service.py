"""Прикладной слой: получение обобщённой сводки и кэширование результата."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from .aggregate import build_report
from .config import Settings
from .models import Report
from .sources import collect_all
from .timeutil import local_now

DEFAULT_TTL_SECONDS = 300.0


async def fetch_report(settings: Settings) -> Report:
    """Опрашивает все площадки и собирает обобщённую сводку."""
    results, skipped = await collect_all(settings)
    return build_report(settings, results, skipped=skipped)


class ReportCache:
    """Кэш сводки с временем жизни, чтобы не дёргать площадки на каждое сообщение."""

    def __init__(self, settings: Settings, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> None:
        self.settings = settings
        self.ttl = timedelta(seconds=max(0.0, ttl_seconds))
        self._lock = asyncio.Lock()
        self._report: Report | None = None

    @property
    def cached(self) -> Report | None:
        return self._report

    async def get(self, force: bool = False) -> Report:
        """Возвращает сводку из кэша или запрашивает новую у площадок."""
        async with self._lock:
            report = self._report
            if not force and report is not None:
                age = local_now(self.settings.timezone) - report.generated_at
                if age < self.ttl:
                    return report
            self._report = await fetch_report(self.settings)
            return self._report
