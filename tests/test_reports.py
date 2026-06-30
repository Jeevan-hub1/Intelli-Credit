"""Unit tests for regulatory reporting (Requirement 22)."""
from config.storage import storage
from services.reports import (
    PortfolioLoan,
    build_portfolio_report,
    classify_npa,
    export_excel,
    export_pdf,
)


def test_npa_classification_bands():
    assert classify_npa(0) == "Standard"
    assert classify_npa(45) == "SMA"
    assert classify_npa(200) == "Substandard"
    assert classify_npa(700) == "Doubtful"
    assert classify_npa(2000) == "Loss"


def _loans():
    return [
        PortfolioLoan("b1", "Acme", "manufacturing", 8_000_000, days_past_due=120, overall_score=55),
        PortfolioLoan("b2", "Beta", "retail", 2_000_000, days_past_due=0, overall_score=78),
    ]


def test_build_portfolio_report_npa_and_sector():
    report = build_portfolio_report(_loans(), period="2026-06", capital_base=50_000_000)
    assert report.total_exposure == 10_000_000
    assert report.npa_breakdown["Substandard"] == 8_000_000
    assert report.npa_breakdown["Standard"] == 2_000_000
    assert set(report.sector_exposure) == {"manufacturing", "retail"}


def test_exposure_limit_breach_flagged():
    # capital base 50M -> RBI limit 15% = 7.5M; b1 at 8M breaches (>=90% of limit).
    report = build_portfolio_report(_loans(), period="2026-06", capital_base=50_000_000)
    assert any(b["borrower_id"] == "b1" for b in report.exposure_limit_breaches)


def test_export_excel_and_pdf_persisted():
    report = build_portfolio_report(_loans(), period="2026-06", capital_base=50_000_000)
    excel_key = export_excel(report)
    pdf_key = export_pdf(report)
    assert storage.exists(excel_key)
    assert storage.exists(pdf_key)
    # Excel/CSV content contains the NPA classification header.
    assert b"NPA Class" in storage.get(excel_key)
