"""Temporal decay utilities (Requirement 14.4).

Implements exponential weighting w(t) = e^(-lambda * age_days) with the
data-type specific decay constants defined in the requirements, plus helpers
for recency classification (Requirement 28.3) and weighted aggregation.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from enum import Enum
from typing import Iterable, Sequence


class DataType(str, Enum):
    """Data categories with distinct temporal decay constants."""

    FINANCIAL_STATEMENT = "financial_statement"
    GST = "gst"
    BANK_STATEMENT = "bank_statement"
    NEWS = "news"
    LITIGATION = "litigation"
    OFFICER_NOTE = "officer_note"
    RATING_REPORT = "rating_report"


# Lambda decay constants per Requirement 14.4.
DECAY_LAMBDA: dict[DataType, float] = {
    DataType.FINANCIAL_STATEMENT: 0.001,
    DataType.GST: 0.002,
    DataType.BANK_STATEMENT: 0.003,
    DataType.NEWS: 0.010,
    DataType.LITIGATION: 0.005,
    DataType.OFFICER_NOTE: 0.0005,
    DataType.RATING_REPORT: 0.003,
}


def _age_in_days(point: date | datetime, *, reference: datetime | None = None) -> float:
    """Return non-negative age in days between a point in time and a reference."""
    ref = reference or datetime.now(timezone.utc)
    if isinstance(point, datetime):
        dt = point if point.tzinfo else point.replace(tzinfo=timezone.utc)
    else:
        dt = datetime(point.year, point.month, point.day, tzinfo=timezone.utc)
    age_days = (ref - dt).total_seconds() / 86400.0
    return max(0.0, age_days)


def decay_weight(
    data_type: DataType,
    point: date | datetime,
    *,
    reference: datetime | None = None,
) -> float:
    """Compute exponential decay weight for a data point of a given type."""
    lam = DECAY_LAMBDA[data_type]
    age_days = _age_in_days(point, reference=reference)
    return math.exp(-lam * age_days)


def weighted_average(
    values: Sequence[float],
    weights: Sequence[float],
) -> float:
    """Compute a weighted average; returns 0.0 if total weight is zero."""
    if len(values) != len(weights):
        raise ValueError("values and weights must have equal length")
    total_w = sum(weights)
    if total_w == 0:
        return 0.0
    return sum(v * w for v, w in zip(values, weights)) / total_w


class Recency(str, Enum):
    """Recency classification for research findings (Requirement 28.3)."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


def classify_recency(point: date | datetime, *, reference: datetime | None = None) -> Recency:
    """Classify recency: <=30d HIGH, 30-180d MEDIUM, >180d LOW."""
    age_days = _age_in_days(point, reference=reference)
    if age_days <= 30:
        return Recency.HIGH
    if age_days <= 180:
        return Recency.MEDIUM
    return Recency.LOW


# Fixed-position weights for 3-year revenue trend (Requirement 10.4):
# most recent 50%, prior 30%, two-years-prior 20%.
THREE_YEAR_TREND_WEIGHTS: tuple[float, float, float] = (0.50, 0.30, 0.20)


def three_year_trend_score(values_recent_first: Sequence[float]) -> float:
    """Weighted blend of up to 3 yearly values, most-recent-first."""
    vals = list(values_recent_first)[:3]
    weights = list(THREE_YEAR_TREND_WEIGHTS[: len(vals)])
    return weighted_average(vals, weights)
