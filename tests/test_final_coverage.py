"""Final targeted tests to close remaining coverage branches."""

from datetime import date, datetime, timezone

import pytest

from models.application import CollateralItem, LoanRequest
from models.base import FiveCDimension, Sentiment, Severity
from models.scoring import ComplianceCheck, QualitativeNote
from services import ews_monitor as ews
from services import external_apis as ext
from services import gst_parser as gp
from services import scoring as sc
from services.bank_parser import _to_date
from services.financial_parser import parse_financials
from utils.temporal import DataType, decay_weight


def test_character_noncompliant_penalty():
    ctx = sc.ScoringContext(
        compliance=ComplianceCheck(
            is_compliant=False, violations=["overdue filing", "disqualified director"]
        )
    )
    dim = sc.score_character(ctx)
    assert any("MCA21" in e.component for e in dim.explainability)


def test_capital_high_debt_to_equity_penalty():
    fa = parse_financials(
        [
            {
                "fiscal_year": 2023,
                "balance_sheet": {
                    "total_assets": 400,
                    "total_liabilities": 360,
                    "equity": 40,
                    "net_worth": 40,
                    "total_debt": 200,
                },
                "profit_and_loss": {"revenue": 100},
            }
        ]
    )
    dim = sc.score_capital(sc.ScoringContext(financials=fa))
    assert any("Debt-to-Equity" in e.component for e in dim.explainability)


def test_collateral_high_ltv_capped():
    loan = LoanRequest(
        amount=5000,
        tenure_months=12,
        collateral=[
            CollateralItem(description="P", collateral_type="inventory", market_value=6000)
        ],
    )
    dim = sc.score_collateral(sc.ScoringContext(loan_request=loan))
    assert dim.score <= 45  # LTV over 75% after 40% inventory haircut


def test_qualitative_note_without_dimension_is_skipped():
    dims = [sc._dim(FiveCDimension.CHARACTER, 50, [])]
    note = QualitativeNote(
        officer_id="o",
        text="general",
        dimension=None,
        sentiment=Sentiment.NEUTRAL,
        severity=Severity.LOW,
    )
    sc.apply_qualitative_notes(dims, [note])  # score_impact evaluated, no target -> continue
    assert dims[0].score == 50


def test_synthesize_with_notes():
    note = QualitativeNote(
        officer_id="o",
        text="great mgmt",
        dimension=FiveCDimension.CHARACTER,
        sentiment=Sentiment.POSITIVE,
        severity=Severity.MEDIUM,
    )
    score = sc.synthesize(sc.ScoringContext(), notes=[note])
    assert score.overall_score >= 0


def test_ews_medium_severity():
    alert = ews.evaluate_alert("b", ews.detect_triggers(gst_filing_gaps=2))
    assert alert.severity == ews.Severity.MEDIUM


def test_gstin_non_digit_state():
    assert not ext.validate_gstin("ABCDEFGHIJKLMNO")  # 15 chars, non-digit state code


def test_gst_customers_from_3b_entries():
    gd = gp.parse_gst(
        [
            {
                "form_type": "GSTR-3B",
                "period": "2024-01",
                "gstin": "27A",
                "total_taxable_value": 1000,
                "total_itc": 100,
                "entries": [{"gstin": "27CUST1", "taxable_value": 500}],
            }
        ]
    )
    assert "27CUST1" in gd.customer_gstins


def test_to_date_from_datetime():
    assert _to_date(datetime(2024, 1, 5, 12, 0, tzinfo=timezone.utc)) == date(2024, 1, 5)


def test_decay_weight_naive_and_date():
    naive = datetime(2024, 1, 1)
    assert 0 < decay_weight(DataType.GST, naive) <= 1.0
    assert 0 < decay_weight(DataType.GST, date(2024, 1, 1)) <= 1.0


def test_security_valid_fernet_key():
    from cryptography.fernet import Fernet

    from config.settings import settings
    from services import security

    old = settings.encryption_key
    settings.encryption_key = Fernet.generate_key().decode()  # valid 44-char key
    try:
        assert security.decrypt_text(security.encrypt_text("x")) == "x"
    finally:
        settings.encryption_key = old


def test_rate_limit_backend_and_client_key():
    from types import SimpleNamespace

    from api.deps import InMemoryRateLimitBackend, client_key

    be = InMemoryRateLimitBackend()
    assert be.hit("c", 1, 60) == (True, 0)
    allowed, retry = be.hit("c", 1, 60)
    assert not allowed and retry >= 1
    # window=0 forces the expiry/popleft branch on the next hit.
    assert be.hit("d", 1, 0) == (True, 0)
    assert be.hit("d", 1, 0) == (True, 0)

    req_hdr = SimpleNamespace(headers={"x-api-client": "abc"}, client=None)
    assert client_key(req_hdr) == "abc"
    req_ip = SimpleNamespace(headers={}, client=SimpleNamespace(host="1.2.3.4"))
    assert client_key(req_ip) == "1.2.3.4"
    req_anon = SimpleNamespace(headers={}, client=None)
    assert client_key(req_anon) == "anon"


def test_rate_limiter_raises_429(monkeypatch):
    from types import SimpleNamespace

    from fastapi import HTTPException

    from api import deps
    from config.settings import settings

    monkeypatch.setattr(settings, "rate_limit_per_minute", 1)
    deps.set_backend(deps.InMemoryRateLimitBackend())
    req = SimpleNamespace(headers={"x-api-client": "rl"}, client=None)
    deps.rate_limiter(req)
    with pytest.raises(HTTPException) as exc:
        deps.rate_limiter(req)
    assert exc.value.status_code == 429
    deps.set_backend(deps.InMemoryRateLimitBackend())  # reset


def test_classify_with_hint():
    from services.document_parser import DocumentType, classify

    res = classify("irrelevant", hint=DocumentType.GST_RETURN)
    assert res.doc_type == DocumentType.GST_RETURN and res.confidence == 0.99


def test_gst_skips_empty_gstin_entries():
    gd = gp.parse_gst(
        [
            {
                "form_type": "GSTR-2A",
                "period": "2024-01",
                "gstin": "27A",
                "total_itc": 10,
                "entries": [
                    {"gstin": "", "taxable_value": 100},
                    {"gstin": "27SUP", "taxable_value": 200},
                ],
            }
        ]
    )
    assert "27SUP" in gd.supplier_gstins and "" not in gd.supplier_gstins


def test_cam_recommendation_reasons_variants():
    from models.application import Application, Borrower, LoanRequest
    from models.base import Severity
    from models.scoring import FraudFinding, FraudReport
    from services.cam_generator import CAMInputs, generate_cam
    from services.scoring import ScoringContext, synthesize

    app = Application(
        borrower=Borrower(name="Co", industry="mfg"),
        loan_request=LoanRequest(amount=1000, tenure_months=12),
    )
    # Clean approve -> "indicators support the decision" reason branch.
    good_fin = parse_financials(
        [
            {
                "fiscal_year": 2023,
                "balance_sheet": {
                    "total_assets": 5000,
                    "total_liabilities": 2000,
                    "equity": 3000,
                    "net_worth": 3000,
                    "total_debt": 1000,
                    "current_assets": 3000,
                    "current_liabilities": 500,
                },
                "profit_and_loss": {
                    "revenue": 10000,
                    "ebitda": 3000,
                    "ebit": 2500,
                    "depreciation": 500,
                    "interest_expense": 200,
                    "net_profit": 1800,
                },
                "cash_flow": {"operating_cash_flow": 2000, "debt_repayment": 200},
            }
        ]
    )
    from models.application import CollateralItem

    app.loan_request.collateral = [
        CollateralItem(description="P", collateral_type="property", market_value=10000)
    ]
    score = synthesize(
        ScoringContext(
            loan_request=app.loan_request,
            financials=good_fin,
            compliance=None,
            litigation=None,
            industry_growth_pct=12,
            promoter_score=90,
        )
    )
    cam_clean = generate_cam(CAMInputs(application=app, credit_score=score, financials=good_fin))
    # Critical fraud -> "Critical fraud signals present" reason branch.
    fraud = FraudReport(
        findings=[
            FraudFinding(kind="circular_trading", severity=Severity.CRITICAL, description="x")
        ],
        has_critical=True,
    )
    score2 = synthesize(
        ScoringContext(loan_request=app.loan_request, financials=good_fin, fraud=fraud)
    )
    cam_fraud = generate_cam(
        CAMInputs(application=app, credit_score=score2, financials=good_fin, fraud=fraud)
    )
    reasons = cam_fraud.section("recommendation").data_points["reasons"]
    assert any("fraud" in r.lower() for r in reasons)
    # cam_clean here has incomplete data (no gst/bank), so it carries a
    # low-confidence reason; the clean "indicators support" branch is covered
    # by test_cam_clean_recommendation_full_completeness.
    clean_reasons = cam_clean.section("recommendation").data_points["reasons"]
    assert clean_reasons  # at least one reason is always present


def test_store_load_result_and_list_no_db():
    from api import store

    assert store.load_result("missing-app") is None
    assert store.list_cam_versions("missing-app") == []  # db=None -> []


def test_ews_high_severity_single_trigger():
    # Four filing gaps -> one MEDIUM trigger weighted x4 = 48 -> base HIGH, not escalated.
    alert = ews.evaluate_alert("b", ews.detect_triggers(gst_filing_gaps=4))
    assert alert.severity == ews.Severity.HIGH and alert.escalated is False


def test_cam_clean_recommendation_full_completeness():
    from models.application import Application, Borrower, CollateralItem, LoanRequest
    from models.scoring import ComplianceCheck, FraudReport, LitigationReport
    from services.bank_parser import parse_bank_statement
    from services.cam_generator import CAMInputs, generate_cam
    from services.gst_parser import parse_gst
    from services.scoring import ScoringContext, synthesize

    fin = parse_financials(
        [
            {
                "fiscal_year": 2023,
                "balance_sheet": {
                    "total_assets": 5000,
                    "total_liabilities": 1500,
                    "equity": 3500,
                    "net_worth": 3500,
                    "total_debt": 800,
                    "current_assets": 3000,
                    "current_liabilities": 400,
                },
                "profit_and_loss": {
                    "revenue": 10000,
                    "ebitda": 3500,
                    "ebit": 3000,
                    "depreciation": 500,
                    "interest_expense": 150,
                    "net_profit": 2200,
                },
                "cash_flow": {"operating_cash_flow": 2500, "debt_repayment": 150},
            }
        ]
    )
    loan = LoanRequest(
        amount=1000,
        tenure_months=12,
        collateral=[
            CollateralItem(description="P", collateral_type="property", market_value=20000)
        ],
    )
    gst = parse_gst(
        [
            {
                "form_type": "GSTR-3B",
                "period": "2024-01",
                "gstin": "27A",
                "total_taxable_value": 10000,
                "total_itc": 1000,
            }
        ]
    )
    bank = parse_bank_statement(
        {
            "account_number": "1",
            "transactions": [
                {"date": "2024-01-05", "description": "Salary", "credit": 5000, "balance": 50000}
            ],
        }
    )
    ctx = ScoringContext(
        loan_request=loan,
        financials=fin,
        gst=gst,
        bank=bank,
        compliance=ComplianceCheck(),
        litigation=LitigationReport(),
        fraud=FraudReport(),
        industry_growth_pct=12,
        promoter_score=95,
    )
    score = synthesize(ctx)
    assert not score.low_confidence and not score.critical_weaknesses
    app = Application(borrower=Borrower(name="Co", industry="mfg"), loan_request=loan)
    cam = generate_cam(CAMInputs(application=app, credit_score=score, financials=fin))
    reasons = cam.section("recommendation").data_points["reasons"]
    assert any("indicators support" in r.lower() for r in reasons)
