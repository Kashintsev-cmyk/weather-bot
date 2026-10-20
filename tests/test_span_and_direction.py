"""Регресс-тесты для BLOCKER findings #1, #2 (B9.2 ручной фикс)."""

from __future__ import annotations

from datetime import datetime, timezone

from app.aggregate import _direction_consensus
from app.events import _span


def _hour(h: int) -> object:
    """Простой объект с .time — только для _span."""
    return type("_H", (), {"time": datetime(2026, 1, 1, h, tzinfo=timezone.utc)})()


# --- BLOCKER #1: _span для 3-часового шага ---


def test_span_three_hour_step() -> None:
    """OWM (3-часовой шаг): конец = last + 3h, не +1h."""
    start, end = _span([_hour(13), _hour(16), _hour(19)])
    assert start == datetime(2026, 1, 1, 13, tzinfo=timezone.utc)
    assert end == datetime(2026, 1, 1, 22, tzinfo=timezone.utc)


def test_span_one_hour_step() -> None:
    _, end = _span([_hour(13), _hour(14), _hour(15)])
    assert end == datetime(2026, 1, 1, 16, tzinfo=timezone.utc)


def test_span_single_item_defaults_to_one_hour() -> None:
    _, end = _span([_hour(13)])
    assert end == datetime(2026, 1, 1, 14, tzinfo=timezone.utc)


# --- BLOCKER #2: _direction_consensus для 350°/10° ---


class _Sample:
    def __init__(self, d: float) -> None:
        self.wind_direction = d


def test_direction_consensus_wraps_small_spread() -> None:
    """350° и 10° — spread 20°, не 340°."""
    items = [("a", _Sample(350)), ("b", _Sample(10))]
    result = _direction_consensus(items, "wind_direction")
    assert result.median is not None
    # median ≈ 0 (или 360)
    assert min(abs(result.median), abs(result.median - 360)) < 1
    # spread = high - low ≈ 20
    assert abs((result.high - result.low) - 20) < 1


def test_direction_consensus_no_wrap() -> None:
    """100°, 110°, 120° — обычный spread 20°."""
    items = [("a", _Sample(100)), ("b", _Sample(110)), ("c", _Sample(120))]
    result = _direction_consensus(items, "wind_direction")
    assert abs((result.median or 0) - 110) < 1
    assert abs((result.high - result.low) - 20) < 1


def test_direction_consensus_empty() -> None:
    """Пустой список — не падаем, result = consensus([])."""
    result = _direction_consensus([], "wind_direction")
    assert result.median is None
