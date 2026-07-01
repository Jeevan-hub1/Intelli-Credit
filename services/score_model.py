"""Pluggable credit score model (Requirement 25 - model governance).

The scoring engine's overall number can be produced either by the transparent
`HeuristicScoreModel` (the weighted Five Cs sum, the default) or by a trained
`SklearnScoreModel` loaded from an artifact. A `backtest` harness computes
portfolio metrics so a candidate model can be evaluated before promotion.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Sequence

from utils.logging import get_logger
from utils.numeric import clamp, safe_div

logger = get_logger(__name__)


class ScoreModel(ABC):
    """Interface for models that produce an overall 0-100 credit score."""

    name: str = "base"

    @abstractmethod
    def predict_overall(self, dimensions: Sequence[Any], features: dict) -> float:
        """Return the overall score from Five Cs dimensions + extra features."""


class HeuristicScoreModel(ScoreModel):
    """Transparent weighted sum of the Five Cs dimension scores (default)."""

    name = "heuristic-1.0"

    def predict_overall(self, dimensions: Sequence[Any], features: dict) -> float:
        total = sum(d.score * d.weight for d in dimensions)
        return round(clamp(total), 2)


class SklearnScoreModel(ScoreModel):  # pragma: no cover - requires joblib + a model artifact
    """Loads a trained scikit-learn/joblib model and predicts the overall score."""

    name = "sklearn"

    def __init__(self, model_path: str) -> None:
        import joblib

        self._model = joblib.load(model_path)
        self.name = f"sklearn:{os.path.basename(model_path)}"

    def predict_overall(self, dimensions: Sequence[Any], features: dict) -> float:
        vector = [d.score for d in dimensions] + [
            float(v) for v in features.values() if isinstance(v, (int, float))
        ]
        pred = float(self._model.predict([vector])[0])
        return round(clamp(pred), 2)


def load_score_model() -> ScoreModel:
    """Select the active score model from configuration (MODEL_PATH -> sklearn)."""
    model_path = os.getenv("SCORE_MODEL_PATH")
    if model_path:  # pragma: no cover - requires joblib + artifact
        try:
            return SklearnScoreModel(model_path)
        except Exception as exc:
            logger.warning("Score model load failed (%s); using heuristic", exc)
    return HeuristicScoreModel()


@dataclass
class BacktestResult:
    """Metrics from evaluating a score model against a labelled dataset."""

    model_name: str
    sample_size: int
    approval_rate: float
    approved_default_rate: float
    rejected_default_rate: float
    discrimination: float  # mean score gap between good and defaulted borrowers
    threshold: float


def backtest(
    model: ScoreModel,
    dataset: Sequence[dict],
    *,
    approve_threshold: float = 65.0,
) -> BacktestResult:
    """Backtest a model against labelled outcomes (Requirement 25).

    Each row: ``{"dimensions": [DimensionScore...], "features": {...},
    "defaulted": bool}``. Reports approval rate, default rates for approved vs
    rejected, and score discrimination between good and defaulted borrowers.
    """
    approved = rejected = approved_defaults = rejected_defaults = 0
    good_scores: list[float] = []
    bad_scores: list[float] = []

    for row in dataset:
        score = model.predict_overall(row["dimensions"], row.get("features", {}))
        defaulted = bool(row.get("defaulted"))
        (bad_scores if defaulted else good_scores).append(score)
        if score >= approve_threshold:
            approved += 1
            approved_defaults += int(defaulted)
        else:
            rejected += 1
            rejected_defaults += int(defaulted)

    n = len(dataset)
    mean_good = safe_div(sum(good_scores), len(good_scores))
    mean_bad = safe_div(sum(bad_scores), len(bad_scores))
    return BacktestResult(
        model_name=model.name,
        sample_size=n,
        approval_rate=round(safe_div(approved, n), 4),
        approved_default_rate=round(safe_div(approved_defaults, approved), 4),
        rejected_default_rate=round(safe_div(rejected_defaults, rejected), 4),
        discrimination=round(mean_good - mean_bad, 2),
        threshold=approve_threshold,
    )
