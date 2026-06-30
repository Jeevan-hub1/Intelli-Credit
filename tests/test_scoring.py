"""Tests for the Five Cs scoring module (Requirements 9-14, 26)."""
from models.application import CollateralItem, LoanRequest
from models.base import FiveCDimension, FIVE_C_WEIGHTS, Sentiment, Severity
from models.scoring import (
    ComplianceCheck,
    FraudFinding,
    FraudReport,
    LitigationReport,
    QualitativeNote,
)
from services.financial_parser import parse_financials
from services.scoring import (
    ScoringContext,
    evaluate_qualitative_note,
    score_collateral,
    synthesize,
)


def _ctx(sample_financials, **kw):
    fa = parse_financials(sample_financials)
    loan = LoanRequest(amount=3000, tenure_months=36,
                       collateral=[CollateralItem(description="P", collateral_type="property",
                                                  market_value=6000)])
    return ScoringContext(loan_request=loan, financials=fa, compliance=ComplianceCheck(),
                          litigation=LitigationReport(), fraud=FraudReport(),
                          model_version="1.0.0", **kw)


def test_weights_sum_to_one():
    assert abs(sum(FIVE_C_WEIGHTS.values()) - 1.0) < 1e-9


def test_synthesize_healthy(sample_financials):
    score = synthesize(_ctx(sample_financials))
    assert 0 <= score.overall_score <= 100
    assert len(score.dimensions) == 5
    assert score.recommendation.value in ("Approve", "Approve with Conditions", "Reject")


def test_collateral_ltv_cap(sample_financials):
    # LTV = 3000 / (6000 * 0.75) = 0.667 -> within limit, score should be decent
    dim = score_collateral(_ctx(sample_financials))
    assert dim.dimension == FiveCDimension.COLLATERAL
    assert dim.score > 45


def test_critical_fraud_forces_reject(sample_financials):
    fraud = FraudReport(findings=[FraudFinding(kind="circular_trading", severity=Severity.CRITICAL,
                                               description="x")], has_critical=True)
    ctx = _ctx(sample_financials)
    ctx.fraud = fraud
    score = synthesize(ctx)
    assert score.recommendation.value == "Reject"


def test_qualitative_note_impact_bounds():
    adverse = evaluate_qualitative_note(QualitativeNote(officer_id="o", text="t",
                                        sentiment=Sentiment.ADVERSE, severity=Severity.CRITICAL))
    positive = evaluate_qualitative_note(QualitativeNote(officer_id="o", text="t",
                                         sentiment=Sentiment.POSITIVE, severity=Severity.CRITICAL))
    assert adverse == -15.0
    assert positive == 8.0


def test_low_confidence_when_incomplete(sample_financials):
    # Only financials + loan present -> completeness below 70%
    score = synthesize(_ctx(sample_financials))
    assert score.data_completeness < 0.70
    assert score.low_confidence
