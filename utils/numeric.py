"""Safe numeric helpers used across scoring and parsing services."""

from __future__ import annotations

from typing import Optional


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Divide guarding against zero/None denominators."""
    if not denominator:
        return default
    return numerator / denominator


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    """Clamp a value into an inclusive range."""
    return max(low, min(high, value))


def pct_change(current: float, previous: float) -> Optional[float]:
    """Percentage change from previous to current; None if previous is zero."""
    if not previous:
        return None
    return (current - previous) / abs(previous) * 100.0


def cagr(begin: float, end: float, periods: int) -> Optional[float]:
    """Compound annual growth rate over a number of periods (as a percent)."""
    if begin <= 0 or periods <= 0:
        return None
    return ((end / begin) ** (1.0 / periods) - 1.0) * 100.0
