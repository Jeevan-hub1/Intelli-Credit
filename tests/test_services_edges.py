"""Edge-branch coverage for services."""

from datetime import date

import pytest

from services import bank_parser as bp
from services import document_parser as dp
from services import external_apis as ext
from services import financial_parser as fp
from services import gst_parser as gp
from services import scoring as sc
from services.feature_store import FeatureStore

# --------------------------- document_parser ------------------------------


def test_detect_format_magic_bytes():
    assert dp.detect_format("noext", b"%PDF-1.4") == dp.DocumentFormat.PDF
    assert dp.detect_format("noext", b"PK\x03\x04") == dp.DocumentFormat.EXCEL
    assert dp.detect_format("noext", b"\x89PNG\r\n\x1a\n") == dp.DocumentFormat.IMAGE


def test_detect_format_unsupported_raises():
    with pytest.raises(dp.DocumentError) as exc:
        dp.detect_format("mystery.xyz", b"garbage")
    assert exc.value.code == "ERR_UNSUPPORTED_FORMAT"


def test_classify_unknown_returns_zero():
    res = dp.classify("nothing relevant here")
    assert res.doc_type == dp.DocumentType.UNKNOWN and res.confidence == 0.0


# --------------------------- financial_parser -----------------------------


def test_financial_num_coercion_and_negative_revenue():
    fa = fp.parse_financials(
        [
            {
                "fiscal_year": 2023,
                "balance_sheet": {
                    "total_assets": "notanumber",
                    "total_liabilities": 0,
                    "equity": 0,
                },
                "profit_and_loss": {"revenue": -100},
            }
        ]
    )
    codes = {f.code for f in fa.latest.flags}
    assert "NEGATIVE_REVENUE" in codes


def test_detect_standard_variants():
    assert fp.detect_standard({"standard": "Ind AS"}).value == "ind_as"
    assert fp.detect_standard({"standard": "Indian GAAP"}).value == "indian_gaap"
    assert fp.detect_standard({"notes": ["Other Comprehensive Income"]}).value == "ind_as"
    assert fp.detect_standard({}).value == "unknown"


def test_low_line_item_confidence_flag():
    fa = fp.parse_financials(
        [
            {
                "fiscal_year": 2023,
                "balance_sheet": {
                    "total_assets": 100,
                    "total_liabilities": 60,
                    "equity": 40,
                    "confidences": {"total_assets": 0.5},
                },
                "profit_and_loss": {"revenue": 100},
            }
        ]
    )
    assert any(f.code == "LOW_LINE_ITEM_CONFIDENCE" for f in fa.latest.flags)


# ------------------------------ bank_parser -------------------------------


def test_bank_date_formats_and_bad_date():
    assert bp._to_date("2024-01-05") == date(2024, 1, 5)
    assert bp._to_date("05-01-2024") == date(2024, 1, 5)
    assert bp._to_date(date(2024, 1, 5)) == date(2024, 1, 5)
    with pytest.raises(ValueError):
        bp._to_date("not-a-date")


def test_bank_num_coercion():
    assert bp._num(None) == 0.0
    assert bp._num("bad") == 0.0
    assert bp._num("12.5") == 12.5


def test_categorize_below_threshold_uncategorized():
    # 'revenue_receipt' rule has confidence 0.8 (== threshold, kept); ensure
    # an unmatched description is uncategorized.
    assert bp.categorize("zzz nothing")[0] == "uncategorized"
    assert bp.categorize("Salary")[0] == "salary"


def test_empty_bank_statement_conduct():
    stmt = bp.parse_bank_statement({"account_number": "1", "transactions": []})
    assert stmt.conduct.monthly_average_balance == 0.0


# ------------------------------ gst_parser --------------------------------


def test_gst_num_coercion_and_no_recon():
    gd = gp.parse_gst(
        [
            {
                "form_type": "GSTR-3B",
                "period": "2024-01",
                "gstin": "27A",
                "total_taxable_value": "bad",
                "total_itc": None,
            }
        ]
    )
    assert gd.reconciliations == []  # only 3B, no 2A to reconcile


# ----------------------------- external_apis ------------------------------


def test_gstin_edge_cases():
    assert not ext.validate_gstin("")
    assert not ext.validate_gstin("27AAPFU0939F1Z")  # 14 chars
    assert not ext.validate_gstin("27@APFU0939F1ZV")  # bad PAN section


def test_days_overdue_unparseable():
    assert ext._days_overdue("garbage") == 0
    assert ext._days_overdue(None) == 0


def test_ecourts_ibc_and_compliant():
    rep = ext.search_ecourts(
        "Co",
        cases=[
            {"case_id": "1", "category": "insolvency", "title": "IBC petition", "monetary_value": 0}
        ],
    )
    assert rep.ibc_count == 1
    comp = ext.check_mca21_compliance("U1", filings=[], directors=[])
    assert comp.is_compliant


# ------------------------------- scoring ----------------------------------


def test_scoring_no_financials_guards():
    ctx = sc.ScoringContext()
    assert sc.score_capacity(ctx).score == 0.0
    assert sc.score_capital(ctx).score == 0.0


def test_score_collateral_no_collateral():
    from models.application import LoanRequest

    ctx = sc.ScoringContext(loan_request=LoanRequest(amount=100, tenure_months=12))
    dim = sc.score_collateral(ctx)
    assert dim.score == 30.0


def test_conditions_adverse_news_penalty():
    from models.base import Sentiment
    from models.scoring import ResearchFinding

    research = [
        ResearchFinding(
            title="t",
            summary="s",
            url="u",
            source_authority="news",
            recency="HIGH",
            sentiment=Sentiment.ADVERSE,
        )
        for _ in range(4)
    ]
    ctx = sc.ScoringContext(research=research)
    dim = sc.score_conditions(ctx)
    assert any("News" in e.component for e in dim.explainability)


def test_capital_declining_networth():
    fa = fp.parse_financials(
        [
            {
                "fiscal_year": 2023,
                "balance_sheet": {
                    "total_assets": 100,
                    "total_liabilities": 60,
                    "equity": 40,
                    "net_worth": 40,
                    "total_debt": 30,
                },
                "profit_and_loss": {"revenue": 100},
            },
            {
                "fiscal_year": 2022,
                "balance_sheet": {
                    "total_assets": 100,
                    "total_liabilities": 60,
                    "equity": 50,
                    "net_worth": 50,
                    "total_debt": 30,
                },
                "profit_and_loss": {"revenue": 100},
            },
            {
                "fiscal_year": 2021,
                "balance_sheet": {
                    "total_assets": 100,
                    "total_liabilities": 60,
                    "equity": 60,
                    "net_worth": 60,
                    "total_debt": 30,
                },
                "profit_and_loss": {"revenue": 100},
            },
        ]
    )
    ctx = sc.ScoringContext(financials=fa)
    dim = sc.score_capital(ctx)
    assert any("Net Worth" in e.component for e in dim.explainability)


def test_negative_networth_penalty():
    fa = fp.parse_financials(
        [
            {
                "fiscal_year": 2023,
                "balance_sheet": {
                    "total_assets": 100,
                    "total_liabilities": 160,
                    "equity": -60,
                    "net_worth": -60,
                    "total_debt": 120,
                },
                "profit_and_loss": {"revenue": 100},
            }
        ]
    )
    ctx = sc.ScoringContext(financials=fa)
    assert sc.score_capital(ctx).score <= 60


# ---------------------------- feature_store -------------------------------


def test_feature_store_version_not_found(tmp_path):
    fs = FeatureStore(root=str(tmp_path / "fs2"))
    fs.write_features(
        application_id="a1",
        features={"x": 1},
        application_date="2026-06-01",
        borrower_industry="mfg",
    )
    assert fs.read_features("a1", version=99) is None


# ---------------------------- research_agent ------------------------------


def test_research_declared_authority_and_bad_date():
    from services import research_agent as ra

    assert ra.classify_authority("https://x.com/a", declared="government") == "government"
    findings = ra.research_borrower(
        "Co",
        raw_results=[
            {
                "title": "t",
                "snippet": "s",
                "url": "https://news.com/a",
                "published_date": "bad-date",
            }
        ],
    )
    assert findings and findings[0].recency in ("HIGH", "MEDIUM", "LOW")


def test_research_disabled_no_results():
    from config.settings import settings
    from services import research_agent as ra

    old = settings.research_agent_enabled
    settings.research_agent_enabled = False
    try:
        assert ra.research_borrower("Co", raw_results=None) == []
    finally:
        settings.research_agent_enabled = old


# ------------------------------- security ---------------------------------


def test_security_derive_key_with_configured_secret():
    from config.settings import settings
    from services import security

    old = settings.encryption_key
    settings.encryption_key = "some-configured-secret"
    try:
        token = security.encrypt_text("data")
        assert security.decrypt_text(token) == "data"
    finally:
        settings.encryption_key = old


def test_security_revoked_and_type_mismatch():
    from models.base import UserRole
    from services import security

    access = security.create_access_token(subject="u", role=UserRole.ANALYST)
    # Wrong expected type.
    with pytest.raises(ValueError):
        security.decode_access_token(access, expected_type="refresh")
    # Revocation.
    payload = security.decode_access_token(access)
    security.revoke_token(payload["jti"])
    with pytest.raises(ValueError):
        security.decode_access_token(access)


# --------------------------------- ews ------------------------------------


def test_ews_escalate_capped_and_low_base():
    from services import ews_monitor as ews

    assert ews._escalate(ews.Severity.CRITICAL) == ews.Severity.CRITICAL
    assert ews.compute_ews_score([]) == 0.0
    alert = ews.evaluate_alert("b", ews.detect_triggers(gst_filing_gaps=0))
    assert alert.severity == ews.Severity.LOW
