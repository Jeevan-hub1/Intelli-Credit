"""Credit Appraisal Memo generation (Requirements 15, 16, 29, 30)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from models.application import Application
from models.bank import BankStatement
from models.cam import CAM, CAMSection, CAMVersionDelta
from models.financial import FinancialAnalysis
from models.gst import GSTData
from models.scoring import (
    ComplianceCheck,
    CreditScore,
    FraudReport,
    LitigationReport,
    QualitativeNote,
    ResearchFinding,
)
from services import ratios
from services.graph_viz import build_graph_visualization
from utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class CAMInputs:
    """Bundle of all analysis artifacts needed to render a CAM."""

    application: Application
    credit_score: CreditScore
    financials: Optional[FinancialAnalysis] = None
    gst: Optional[GSTData] = None
    bank: Optional[BankStatement] = None
    fraud: Optional[FraudReport] = None
    compliance: Optional[ComplianceCheck] = None
    litigation: Optional[LitigationReport] = None
    research: list[ResearchFinding] = field(default_factory=list)
    qualitative_notes: list[QualitativeNote] = field(default_factory=list)


def _executive_summary(inp: CAMInputs) -> CAMSection:
    app = inp.application
    cs = inp.credit_score
    loan = app.loan_request
    body = (
        f"Recommendation: {cs.recommendation.value}. Requested facility of "
        f"INR {loan.amount:,.0f} over {loan.tenure_months} months for "
        f"{app.borrower.name}. Overall credit score {cs.overall_score:.1f}/100 "
        f"({cs.risk_band.value}), confidence interval "
        f"{cs.confidence_low:.0f}-{cs.confidence_high:.0f}."
    )
    return CAMSection(
        key="executive_summary",
        title="Executive Summary",
        body=body,
        data_points={
            "loan_amount": loan.amount,
            "tenure_months": loan.tenure_months,
            "overall_score": cs.overall_score,
            "recommendation": cs.recommendation.value,
        },
        flags=(["LOW_CONFIDENCE"] if cs.low_confidence else []),
    )


def _company_profile(inp: CAMInputs) -> CAMSection:
    b = inp.application.borrower
    body = (
        f"{b.name} (CIN: {b.cin or 'N/A'}, GSTIN: {b.gstin or 'N/A'}). Industry: "
        f"{b.industry}. {b.business_description or 'No business description provided.'} "
        f"Promoters/Directors: {', '.join(b.director_names) or 'N/A'}."
    )
    return CAMSection(
        key="company_profile",
        title="Company Profile",
        body=body,
        data_points={
            "cin": b.cin,
            "gstin": b.gstin,
            "industry": b.industry,
            "incorporation_date": str(b.incorporation_date) if b.incorporation_date else None,
        },
        citations=["MCA21 master data"],
    )


def _financial_analysis(inp: CAMInputs) -> CAMSection:
    fa = inp.financials
    if not fa or not fa.statements:
        return CAMSection(
            key="financial_analysis",
            title="Financial Analysis",
            body="No financial statements available.",
            flags=["DATA_GAP"],
        )
    rows = []
    citations = []
    for s in fa.years_sorted_desc()[:3]:
        rows.append(
            {
                "fiscal_year": s.fiscal_year,
                "revenue": s.profit_and_loss.revenue,
                "ebitda_margin": round(s.profit_and_loss.ebitda_margin, 4),
                "net_profit": s.profit_and_loss.net_profit,
                "dscr": ratios.dscr(s),
                "icr": ratios.icr(s),
                "debt_to_equity": ratios.debt_to_equity(s),
                "current_ratio": ratios.current_ratio(s),
                "net_worth": s.balance_sheet.net_worth,
            }
        )
        citations.append(f"Financial Statement FY{s.fiscal_year}")
    latest = fa.latest
    body = (
        f"3-year analysis. Latest FY{latest.fiscal_year}: revenue "
        f"INR {latest.profit_and_loss.revenue:,.0f}, DSCR {ratios.dscr(latest):.2f}, "
        f"D/E {ratios.debt_to_equity(latest):.2f}, current ratio "
        f"{ratios.current_ratio(latest):.2f}."
    )
    return CAMSection(
        key="financial_analysis",
        title="3-Year Financial Analysis",
        body=body,
        data_points={"years": rows},
        citations=citations,
    )


def _banking_conduct(inp: CAMInputs) -> CAMSection:
    bank = inp.bank
    if not bank:
        return CAMSection(
            key="banking_conduct",
            title="Banking Conduct Analysis",
            body="No bank statement available.",
            flags=["DATA_GAP"],
        )
    c = bank.conduct
    body = (
        f"Account {bank.account_number}: average monthly balance "
        f"INR {c.monthly_average_balance:,.0f}, peak INR {c.peak_balance:,.0f}, "
        f"minimum INR {c.minimum_balance:,.0f}. Bounced transactions: "
        f"{c.bounced_transaction_count}; overdraft instances: {c.overdraft_instances}."
    )
    return CAMSection(
        key="banking_conduct",
        title="Banking Conduct Analysis",
        body=body,
        data_points=c.model_dump(),
        citations=["Bank Statement"],
    )


def _gst_analysis(inp: CAMInputs) -> CAMSection:
    gst = inp.gst
    if not gst:
        return CAMSection(
            key="gst_analysis",
            title="GST Analysis",
            body="No GST data available.",
            flags=["DATA_GAP"],
        )
    flagged = [r for r in gst.reconciliations if r.flagged]
    body = (
        f"GSTIN {gst.gstin}: ITC-to-revenue ratio {gst.itc_to_revenue_ratio:.1%}. "
        f"{len(flagged)} reconciliation period(s) flagged for ITC mismatch. "
        f"{len(gst.supplier_gstins)} suppliers, {len(gst.customer_gstins)} customers."
    )
    return CAMSection(
        key="gst_analysis",
        title="GST Analysis",
        body=body,
        data_points={
            "itc_to_revenue_ratio": gst.itc_to_revenue_ratio,
            "flagged_periods": [r.period for r in flagged],
            "revenue_trend": [p.model_dump() for p in gst.revenue_trend],
        },
        citations=["GSTR-2A", "GSTR-3B"],
    )


def _five_cs(inp: CAMInputs) -> CAMSection:
    cs = inp.credit_score
    dims = {}
    for d in cs.dimensions:
        dims[d.dimension.value] = {
            "score": d.score,
            "weight": d.weight,
            "critical_weakness": d.is_critical_weakness,
            "explainability": [e.model_dump() for e in d.explainability],
        }
    body = (
        "Five Cs assessment: "
        + ", ".join(f"{d.dimension.value} {d.score:.0f}" for d in cs.dimensions)
        + f". Overall {cs.overall_score:.1f} ({cs.risk_band.value})."
    )
    flags = [f"CRITICAL_WEAKNESS:{c.value}" for c in cs.critical_weaknesses]
    return CAMSection(
        key="five_cs", title="Five Cs Risk Assessment", body=body, data_points=dims, flags=flags
    )


def _fraud_section(inp: CAMInputs) -> CAMSection:
    fraud = inp.fraud
    if not fraud or not fraud.findings:
        return CAMSection(
            key="fraud_findings",
            title="Fraud Detection Findings",
            body="No fraud signals detected.",
        )
    by_sev: dict[str, int] = {}
    lines = []
    for f in fraud.findings:
        by_sev[f.severity.value] = by_sev.get(f.severity.value, 0) + 1
        lines.append(f"[{f.severity.value}] {f.description}")
    body = (
        f"{len(fraud.findings)} finding(s). Circular trading ratio "
        f"{fraud.circular_trading_ratio:.1%}. " + " ".join(lines)
    )
    return CAMSection(
        key="fraud_findings",
        title="Fraud Detection Findings",
        body=body,
        data_points={
            "severity_counts": by_sev,
            "circular_trading_ratio": fraud.circular_trading_ratio,
        },
        flags=(["CRITICAL_FRAUD"] if fraud.has_critical else []),
    )


def _recommendation_section(inp: CAMInputs) -> CAMSection:
    cs = inp.credit_score
    reasons = []
    if cs.critical_weaknesses:
        reasons.append(
            f"Critical weakness in {', '.join(c.value for c in cs.critical_weaknesses)}."
        )
    if inp.fraud and inp.fraud.has_critical:
        reasons.append("Critical fraud signals present.")
    if cs.low_confidence:
        reasons.append("Low data completeness reduces confidence.")
    if not reasons:
        reasons.append("Financial and qualitative indicators support the decision.")
    body = f"Final recommendation: {cs.recommendation.value}. " + " ".join(reasons)
    return CAMSection(
        key="recommendation",
        title="Final Recommendation",
        body=body,
        data_points={"recommendation": cs.recommendation.value, "reasons": reasons},
    )


def _explainability_section(inp: CAMInputs) -> CAMSection:
    """Data gaps and confidence impact (Requirement 16.5)."""
    cs = inp.credit_score
    gaps = []
    if not inp.financials or not inp.financials.statements:
        gaps.append("financial statements")
    if not inp.gst:
        gaps.append("GST data")
    if not inp.bank:
        gaps.append("bank statements")
    body = (
        f"Data completeness {cs.data_completeness:.0%}. "
        + (f"Gaps: {', '.join(gaps)}. " if gaps else "No major data gaps. ")
        + (
            "This is a LOW-CONFIDENCE assessment."
            if cs.low_confidence
            else "Confidence is adequate."
        )
    )
    return CAMSection(
        key="explainability",
        title="Explainability & Data Gaps",
        body=body,
        data_points={"data_completeness": cs.data_completeness, "gaps": gaps},
    )


def _compute_deltas(
    previous: Optional[CAM], current_sections: list[CAMSection], current_score: float
) -> list[CAMVersionDelta]:
    """Compute the delta between the prior CAM version and the new one (Req 30.3)."""
    deltas: list[CAMVersionDelta] = []
    if previous is None:
        return deltas
    if abs(previous.overall_score - current_score) > 0.001:
        deltas.append(
            CAMVersionDelta(
                field="overall_score",
                old_value=str(previous.overall_score),
                new_value=str(current_score),
            )
        )
    prev_bodies = {s.key: s.body for s in previous.sections}
    for s in current_sections:
        if prev_bodies.get(s.key) != s.body:
            deltas.append(
                CAMVersionDelta(
                    field=f"section:{s.key}",
                    old_value=(prev_bodies.get(s.key) or "")[:120],
                    new_value=s.body[:120],
                )
            )
    return deltas


def generate_cam(
    inp: CAMInputs,
    *,
    previous: Optional[CAM] = None,
    generated_by: Optional[str] = None,
    modification_reason: Optional[str] = None,
) -> CAM:
    """Generate a CAM. If `previous` is supplied, a new version with deltas (Req 30)."""
    from models.application import new_id

    cs = inp.credit_score
    sections = [
        _executive_summary(inp),
        _company_profile(inp),
        _financial_analysis(inp),
        _banking_conduct(inp),
        _gst_analysis(inp),
        _five_cs(inp),
        _fraud_section(inp),
        _explainability_section(inp),
        _recommendation_section(inp),
    ]
    version = (previous.version + 1) if previous else 1
    cam = CAM(
        id=new_id("cam"),
        application_id=inp.application.id,
        version=version,
        recommendation=cs.recommendation,
        overall_score=cs.overall_score,
        risk_band=cs.risk_band.value,
        sections=sections,
        graph_visualization=build_graph_visualization(inp.fraud),
        data_completeness=cs.data_completeness,
        low_confidence=cs.low_confidence,
        model_version=cs.model_version,
        generated_by=generated_by,
        modification_reason=modification_reason,
        deltas_from_previous=_compute_deltas(previous, sections, cs.overall_score),
    )
    cam.pdf_key = render_pdf(cam)
    logger.info(
        "Generated CAM %s v%d for application %s (%s)",
        cam.id,
        version,
        inp.application.id,
        cs.recommendation.value,
    )
    return cam


def _render_text(cam: CAM) -> str:
    """Render a plain-text CAM (fallback and basis for PDF)."""
    lines = [
        "CREDIT APPRAISAL MEMO (CAM)",
        f"Application: {cam.application_id}    Version: {cam.version}",
        f"Model Version: {cam.model_version or 'N/A'}    Generated: {cam.generated_at.isoformat()}",
        "=" * 72,
    ]
    for s in cam.sections:
        lines.append(f"\n## {s.title}")
        lines.append(s.body)
        if s.citations:
            lines.append(f"Sources: {', '.join(s.citations)}")
        if s.flags:
            lines.append(f"Flags: {', '.join(s.flags)}")
    if cam.graph_visualization:
        gv = cam.graph_visualization
        lines.append("\n## Circular Trading Graph")
        lines.append(f"Cycle path: {' -> '.join(gv.cycle_path)}")
        lines.append(f"Summary: {gv.cycle_summary}")
        if gv.image_key:
            lines.append(f"Image: {gv.image_key} (>=300 DPI)")
    return "\n".join(lines)


def render_pdf(cam: CAM) -> Optional[str]:
    """Render the CAM to PDF (reportlab) or text fallback; store and return key."""
    from config.storage import storage

    text = _render_text(cam)
    try:
        import io

        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas

        buf = io.BytesIO()
        pdf = canvas.Canvas(buf, pagesize=A4)
        width, height = A4
        y = height - 40
        for line in text.splitlines():
            if y < 40:  # pragma: no cover - page overflow only on very long CAMs
                pdf.showPage()
                y = height - 40
            pdf.setFont("Helvetica", 8)
            pdf.drawString(36, y, line[:110])
            y -= 11
        pdf.save()
        key = storage.put(
            buf.getvalue(), key=f"cams/{cam.id}_v{cam.version}.pdf", content_type="application/pdf"
        )
        return key
    except Exception:  # pragma: no cover - reportlab is installed in this build
        key = storage.put(
            text.encode("utf-8"), key=f"cams/{cam.id}_v{cam.version}.txt", content_type="text/plain"
        )
        logger.info("reportlab unavailable; stored CAM as text at %s", key)
        return key
