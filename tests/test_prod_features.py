"""Tests for Redis rate limiting, secrets/KMS, live API clients, and ML scoring."""

import pytest

from api import deps
from models.base import FIVE_C_WEIGHTS, FiveCDimension
from models.scoring import DimensionScore
from services import external_apis as ext
from services import score_model as sm
from services import secrets


class _FakeRedis:
    def __init__(self, ttl_value: int = 60) -> None:
        self.counts: dict[str, int] = {}
        self.expires: dict[str, int] = {}
        self._ttl_value = ttl_value

    def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    def expire(self, key, window):
        self.expires[key] = window

    def ttl(self, key):
        return self._ttl_value


# ------------------------- Redis rate limiting ----------------------------


def test_redis_rate_limit_allow_then_block():
    be = deps.RedisRateLimitBackend(_FakeRedis(ttl_value=42))
    assert be.hit("c", 2, 60) == (True, 0)
    assert be.hit("c", 2, 60) == (True, 0)
    allowed, retry = be.hit("c", 2, 60)
    assert allowed is False and retry == 42


def test_redis_rate_limit_ttl_fallback_to_window():
    be = deps.RedisRateLimitBackend(_FakeRedis(ttl_value=-1))
    be.hit("c", 1, 30)
    allowed, retry = be.hit("c", 1, 30)
    assert allowed is False and retry == 30  # ttl<=0 -> window


def test_select_rate_limit_backend_default_in_memory(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert isinstance(deps.select_rate_limit_backend(), deps.InMemoryRateLimitBackend)


# ----------------------------- Secrets/KMS --------------------------------


def test_env_secret_provider(monkeypatch):
    provider = secrets.EnvSecretProvider()
    monkeypatch.setenv("MY_SECRET", "value123")
    assert provider.get("MY_SECRET") == "value123"
    # Falls back to a settings attribute when env var is absent.
    assert provider.get("app_name") == secrets.settings.app_name
    assert provider.get("TOTALLY_MISSING", "fallback") == "fallback"


def test_select_provider_default_env(monkeypatch):
    monkeypatch.delenv("SECRET_PROVIDER", raising=False)
    assert isinstance(secrets.select_provider(), secrets.EnvSecretProvider)


def test_set_and_get_secret(monkeypatch):
    monkeypatch.setenv("ANOTHER", "abc")
    secrets.set_provider(secrets.EnvSecretProvider())
    assert secrets.get_secret("ANOTHER") == "abc"


def test_require_strong_secrets(monkeypatch):
    # Strong key: no error in any environment.
    monkeypatch.setattr(secrets.settings, "secret_key", "x" * 40)
    secrets.require_strong_secrets(environment="production")
    # Weak key: warns outside production...
    monkeypatch.setattr(secrets.settings, "secret_key", "short")
    secrets.require_strong_secrets(environment="development")
    # ...and raises in production.
    with pytest.raises(secrets.WeakSecretError):
        secrets.require_strong_secrets(environment="production")


# --------------------- Live MCA21 / eCourts clients -----------------------


def test_mca21_live_fetch_via_injected_fetcher(monkeypatch):
    monkeypatch.setattr(ext.settings, "mca21_api_key", "key", raising=False)
    comp = ext.check_mca21_compliance(
        "U1",
        fetcher=lambda cin: {
            "filings": [{"type": "AR", "due_date": "2020-01-01", "filed": False}],
            "directors": [{"din": "1", "name": "X", "disqualified": True}],
        },
    )
    assert not comp.is_compliant and len(comp.violations) == 2


def test_ecourts_live_fetch_via_injected_fetcher(monkeypatch):
    monkeypatch.setattr(ext.settings, "ecourts_api_key", "key", raising=False)
    rep = ext.search_ecourts(
        "Acme",
        fetcher=lambda name, directors: [
            {"case_id": "1", "category": "criminal", "title": "fraud", "monetary_value": 100}
        ],
    )
    assert rep.high_risk_count == 1


def test_no_api_key_skips_live_fetch(monkeypatch):
    monkeypatch.setattr(ext.settings, "mca21_api_key", None, raising=False)
    monkeypatch.setattr(ext.settings, "ecourts_api_key", None, raising=False)
    assert ext.check_mca21_compliance("U1").is_compliant
    assert ext.search_ecourts("Acme").cases == []


# --------------------------- ML score model -------------------------------


def _dims(score: float):
    return [
        DimensionScore(dimension=d, score=score, weight=FIVE_C_WEIGHTS[d]) for d in FiveCDimension
    ]


def test_heuristic_model_predict_overall():
    model = sm.HeuristicScoreModel()
    # All dimensions at 70 with weights summing to 1.0 -> 70.
    assert model.predict_overall(_dims(70), {}) == 70.0
    assert model.name == "heuristic-1.0"


def test_load_score_model_default(monkeypatch):
    monkeypatch.delenv("SCORE_MODEL_PATH", raising=False)
    assert isinstance(sm.load_score_model(), sm.HeuristicScoreModel)


def test_backtest_metrics():
    model = sm.HeuristicScoreModel()
    dataset = [
        {"dimensions": _dims(80), "features": {}, "defaulted": False},  # approved, good
        {"dimensions": _dims(75), "features": {}, "defaulted": True},  # approved, defaulted
        {"dimensions": _dims(30), "features": {}, "defaulted": True},  # rejected, defaulted
    ]
    result = sm.backtest(model, dataset, approve_threshold=65.0)
    assert result.sample_size == 3
    assert result.approval_rate == round(2 / 3, 4)
    assert result.approved_default_rate == 0.5  # 1 of 2 approved defaulted
    assert result.rejected_default_rate == 1.0  # 1 of 1 rejected defaulted
    assert result.discrimination > 0  # good scores > defaulted scores
    assert result.model_name == "heuristic-1.0"


def test_synthesize_uses_model():
    from services.scoring import ScoringContext, synthesize

    model = sm.HeuristicScoreModel()
    score = synthesize(ScoringContext(), model=model)
    assert 0 <= score.overall_score <= 100
