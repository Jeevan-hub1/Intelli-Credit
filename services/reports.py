"""Regulatory compliance reporting (Requirement 22).

Generates monthly portfolio-quality reports with NPA classification and
quarterly sector-exposure reports, flags loans approaching RBI exposure
limits, and exports to Excel (CSV-compatible) and PDF.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Optional

from config.storage import storage
from utils.logging import get_logger

logger = get_logger(__name__)

# RBI single-borrower exposure ceiling as a fraction of capital base (illustrative).
RBI_EXPOSURE_LIMIT_FRACTION = 0.15


@dataclass
class PortfolioLoan:
    """A loan position in the monitoring portfolio."""

    borrower_id: str
    borrower_name: str
    industry: str
    exposure: float
    days_past_due: int = 0
    overall_score: float = 0.0


def classify_npa(days_past_due: int) -> str:
    """RBI asset classification by days-past-due (Requirement 22.1)."""
    if days_past_due <= 0:
        return "Standard"
    if days_past_due <= 90:
        return "SMA"          # Special Mention Account
    if days_past_due <= 365:
        return "Substandard"
    if days_past_due <= 1095:
        return "Doubtful"
    return "Loss"



@dataclass
class PortfolioReport:
    period: str
    total_exposure: float = 0.0
    npa_breakdown: dict = field(default_factory=dict)
    sector_exposure: dict = field(default_factory=dict)
    exposure_limit_breaches: list[dict] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)


def build_portfolio_report(
    loans: list[PortfolioLoan], *, period: str, capital_base: float = 0.0
) -> PortfolioReport:
    """Build a portfolio quality + sector exposure report (Req 22.1-22.3)."""
    report = PortfolioReport(period=period)
    limit = capital_base * RBI_EXPOSURE_LIMIT_FRACTION if capital_base else 0.0
    for loan in loans:
        cls = classify_npa(loan.days_past_due)
        report.total_exposure += loan.exposure
        report.npa_breakdown[cls] = report.npa_breakdown.get(cls, 0.0) + loan.exposure
        report.sector_exposure[loan.industry] = (
            report.sector_exposure.get(loan.industry, 0.0) + loan.exposure
        )
        if limit and loan.exposure >= 0.9 * limit:
            report.exposure_limit_breaches.append({
                "borrower_id": loan.borrower_id, "exposure": loan.exposure,
                "limit": round(limit, 2),
                "utilization_pct": round(loan.exposure / limit * 100, 1),
            })
        report.rows.append({
            "borrower_id": loan.borrower_id, "borrower_name": loan.borrower_name,
            "industry": loan.industry, "exposure": loan.exposure,
            "days_past_due": loan.days_past_due, "npa_class": cls,
            "overall_score": loan.overall_score,
        })
    report.total_exposure = round(report.total_exposure, 2)
    return report



def export_excel(report: PortfolioReport) -> str:
    """Export the report rows to a CSV (Excel-compatible) file in storage."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Borrower ID", "Borrower Name", "Industry", "Exposure",
                     "Days Past Due", "NPA Class", "Overall Score"])
    for r in report.rows:
        writer.writerow([r["borrower_id"], r["borrower_name"], r["industry"],
                         r["exposure"], r["days_past_due"], r["npa_class"], r["overall_score"]])
    writer.writerow([])
    writer.writerow(["NPA Class", "Exposure"])
    for cls, val in report.npa_breakdown.items():
        writer.writerow([cls, val])
    key = storage.put(buf.getvalue().encode("utf-8"),
                      key=f"reports/portfolio_{report.period}.csv", content_type="text/csv")
    logger.info("Exported portfolio report Excel/CSV to %s", key)
    return key


def export_pdf(report: PortfolioReport) -> str:
    """Export the report to a PDF (reportlab) or text fallback in storage."""
    lines = [f"PORTFOLIO QUALITY REPORT - {report.period}",
             f"Total Exposure: INR {report.total_exposure:,.0f}", "",
             "NPA Classification:"]
    lines += [f"  {k}: INR {v:,.0f}" for k, v in report.npa_breakdown.items()]
    lines += ["", "Sector Exposure:"]
    lines += [f"  {k}: INR {v:,.0f}" for k, v in report.sector_exposure.items()]
    if report.exposure_limit_breaches:
        lines += ["", "RBI Exposure Limit Watch:"]
        lines += [f"  {b['borrower_id']}: {b['utilization_pct']}% of limit"
                  for b in report.exposure_limit_breaches]
    text = "\n".join(lines)
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas

        buf = io.BytesIO()
        pdf = canvas.Canvas(buf, pagesize=A4)
        _, height = A4
        y = height - 40
        for line in text.splitlines():
            if y < 40:
                pdf.showPage(); y = height - 40
            pdf.setFont("Helvetica", 9)
            pdf.drawString(36, y, line[:110]); y -= 13
        pdf.save()
        key = storage.put(buf.getvalue(), key=f"reports/portfolio_{report.period}.pdf",
                          content_type="application/pdf")
    except Exception:
        key = storage.put(text.encode("utf-8"), key=f"reports/portfolio_{report.period}.txt",
                          content_type="text/plain")
    logger.info("Exported portfolio report PDF to %s", key)
    return key
