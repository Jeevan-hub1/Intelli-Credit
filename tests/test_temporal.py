"""Tests for temporal decay utilities (Requirement 14.4, 10.4, 28.3)."""

from datetime import datetime, timedelta, timezone

from utils.temporal import (
    DECAY_LAMBDA,
    DataType,
    Recency,
    classify_recency,
    decay_weight,
    three_year_trend_score,
    weighted_average,
)


def test_decay_constants_match_spec():
    assert DECAY_LAMBDA[DataType.FINANCIAL_STATEMENT] == 0.001
    assert DECAY_LAMBDA[DataType.GST] == 0.002
    assert DECAY_LAMBDA[DataType.BANK_STATEMENT] == 0.003
    assert DECAY_LAMBDA[DataType.NEWS] == 0.010
    assert DECAY_LAMBDA[DataType.LITIGATION] == 0.005
    assert DECAY_LAMBDA[DataType.OFFICER_NOTE] == 0.0005
    assert DECAY_LAMBDA[DataType.RATING_REPORT] == 0.003


def test_decay_weight_decreases_with_age():
    ref = datetime(2024, 6, 1, tzinfo=timezone.utc)
    recent = decay_weight(DataType.NEWS, ref - timedelta(days=1), reference=ref)
    old = decay_weight(DataType.NEWS, ref - timedelta(days=180), reference=ref)
    assert recent > old
    assert 0 < old < recent <= 1.0


def test_three_year_trend_weights():
    # most recent weighted 50%, then 30%, then 20%
    assert three_year_trend_score([100, 0, 0]) == 50.0
    assert three_year_trend_score([0, 100, 0]) == 30.0
    assert three_year_trend_score([0, 0, 100]) == 20.0


def test_weighted_average_zero_weights():
    assert weighted_average([1, 2], [0, 0]) == 0.0


def test_classify_recency():
    ref = datetime(2024, 6, 1, tzinfo=timezone.utc)
    assert classify_recency(ref - timedelta(days=10), reference=ref) == Recency.HIGH
    assert classify_recency(ref - timedelta(days=90), reference=ref) == Recency.MEDIUM
    assert classify_recency(ref - timedelta(days=300), reference=ref) == Recency.LOW
