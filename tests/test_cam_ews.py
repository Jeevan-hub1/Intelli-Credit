"""Tests for CAM generation and EWS (Requirements 15, 17, 18, 29, 30)."""
from models.application import Application, Borrower, LoanRequest
from models.base import Severity
from models.scoring import (
    ComplianceCheck,
    FraudFinding,
    FraudReport,
    LitigationReport,
    TransactionCycle,
)
from services.cam_generator import CAMInputs, generate_cam
from services.ews_monitor import compute_ews_score, daily_digest, detect_triggers, evaluate_alert
from services.financial_parser import parse_financials
from services.scoring import ScoringContext, synthesize


def _app():
    return Application(borrower=Borrower(name="Acme Ltd", cin="U1", industry="mfg"),
                      loan_request=LoanRequest(amount=3000, tenure_months=24))


def test_generate_cam_sections_and_versions(sample_financials):
    fa = parse_financials(sample_financials)
    ctx = ScoringContext(loan_request=_app().loan_request, financials=fa,
                         compliance=ComplianceCheck(), litigation=LitigationReport(),
                         fraud=FraudReport(), model_version="1.0.0")
    score = synthesize(ctx)
    app = _app()
    inp = CAMInputs(application=app, credit_score=score, financials=fa)
    cam = generate_cam(inp, generated_by="o1")
    keys = {s.key for s in cam.sections}
    assert {"executive_summary", "five_cs", "recommendation", "fraud_findings"} <= keys
    assert cam.version == 1 and cam.pdf_key
    cam2 = generate_cam(inp, previous=cam, generated_by="o1", modification_reason="edit")
    assert cam2.version == 2


def test_cam_includes_graph_for_circular_trading(sample_financials):
    fa = parse_financials(sample_financials)
    fraud = FraudReport(findings=[FraudFinding(kind="circular_trading", severity=Severity.HIGH,
        description="cycle", cycles=[TransactionCycle(entity_chain=["A", "B", "A"],
        total_value=2_000_000, span_days=30,
        edges=[{"from": "A", "to": "B", "amount": 1_000_000, "date": "2024-01-01"},
               {"from": "B", "to": "A", "amount": 1_000_000, "date": "2024-01-15"}])])])
    ctx = ScoringContext(loan_request=_app().loan_request, financials=fa, fraud=fraud,
                         compliance=ComplianceCheck(), litigation=LitigationReport())
    score = synthesize(ctx)
    cam = generate_cam(CAMInputs(application=_app(), credit_score=score, financials=fa, fraud=fraud))
    assert cam.graph_visualization is not None
    assert cam.graph_visualization.cycle_path == ["A", "B", "A"]


def test_ews_score_and_escalation():
    triggers = detect_triggers(days_financials_overdue=70, gst_filing_gaps=2,
                               adverse_news_count=3, rating_downgraded=True)
    assert compute_ews_score(triggers) > 0
    alert = evaluate_alert("b1", triggers, exposure_amount=5_000_000)
    assert alert.escalated  # multiple triggers within window
    assert alert.severity == Severity.CRITICAL


def test_daily_digest_ranking():
    a1 = evaluate_alert("b1", detect_triggers(adverse_news_count=1), exposure_amount=100)
    a2 = evaluate_alert("b2", detect_triggers(rating_downgraded=True), exposure_amount=200)
    digest = daily_digest([a1, a2], top_n=10)
    assert digest[0].rank == 1
    assert digest[0].ews_score >= digest[-1].ews_score
