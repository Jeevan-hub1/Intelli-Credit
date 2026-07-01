"""Five Cs credit scoring module (Requirements 9-14, 26).

Each dimension scorer returns a DimensionScore carrying ExplainabilityScore
entries. `synthesize` combines them into the overall weighted score with risk
band, critical-weakness flags, temporal-decay-aware confidence, and a
recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from services.score_model import ScoreModel

from models.application import LoanRequest
from models.bank import BankStatement
from models.base import (
    FIVE_C_WEIGHTS,
    ExplainabilityScore,
    FiveCDimension,
    Recommendation,
    Sentiment,
    Severity,
    score_to_risk_band,
)
from models.financial import FinancialAnalysis
from models.gst import GSTData
from models.scoring import (
    ComplianceCheck,
    CreditScore,
    DimensionScore,
    FraudReport,
    LitigationReport,
    QualitativeNote,
    ResearchFinding,
)
from services import ratios
from utils.logging import get_logger
from utils.numeric import clamp, safe_div
from utils.temporal import three_year_trend_score

logger = get_logger(__name__)

# Collateral haircuts by type (Requirement 12.4).
COLLATERAL_HAIRCUTS: dict[str, float] = {
    "property": 0.25,
    "inventory": 0.40,
    "receivables": 0.30,
    "equipment": 0.35,
}
DEFAULT_HAIRCUT = 0.50

DSCR_MIN = 1.25  # Requirement 10.6
DE_PENALTY_THRESHOLD = 3.0  # Requirement 11.5
LTV_MAX = 0.75  # Requirement 12.5
NEGATIVE_NEWS_THRESHOLD = 0.60  # Requirement 13.5
CRITICAL_WEAKNESS_FLOOR = 30.0  # Requirement 14.3
LOW_CONFIDENCE_THRESHOLD = 0.70  # Requirement 14.6


@dataclass
class ScoringContext:
    """All inputs required to score an application."""

    loan_request: Optional[LoanRequest] = None
    financials: Optional[FinancialAnalysis] = None
    gst: Optional[GSTData] = None
    bank: Optional[BankStatement] = None
    fraud: Optional[FraudReport] = None
    compliance: Optional[ComplianceCheck] = None
    litigation: Optional[LitigationReport] = None
    research: list[ResearchFinding] = field(default_factory=list)
    industry_growth_pct: float = 8.0  # sector intelligence input
    industry_ebitda_margin: float = 0.12  # benchmark for capacity
    promoter_score: float = 70.0  # promoter background (0-100)
    regulatory_risk: float = 0.2  # 0 (benign) - 1 (severe) Req 13.3
    model_version: Optional[str] = None


def _dim(
    dimension: FiveCDimension, score: float, explain: list[ExplainabilityScore]
) -> DimensionScore:
    """Build a DimensionScore with weight and critical-weakness flag."""
    score = round(clamp(score), 2)
    return DimensionScore(
        dimension=dimension,
        score=score,
        weight=FIVE_C_WEIGHTS[dimension],
        is_critical_weakness=score < CRITICAL_WEAKNESS_FLOOR,
        explainability=explain,
    )


def score_character(ctx: ScoringContext) -> DimensionScore:
    """Character (Req 9): MCA21 30%, litigation 25%, promoter 25%, fraud 20%."""
    explain: list[ExplainabilityScore] = []

    # MCA21 compliance sub-score (30%).
    comp = ctx.compliance
    if comp and not comp.is_compliant:
        mca_score = clamp(100 - 20 * len(comp.violations))
    else:
        mca_score = 90.0
    explain.append(
        ExplainabilityScore(
            component="MCA21 Compliance",
            value=mca_score,
            reasoning=(
                "No compliance violations found."
                if (comp and comp.is_compliant) or not comp
                else f"{len(comp.violations)} violation(s): {'; '.join(comp.violations[:3])}"
            ),
            data_points={"violations": comp.violations if comp else []},
            source_citations=["MCA21 filing history"],
        )
    )

    # Litigation sub-score (25%).
    lit = ctx.litigation
    lit_penalty = 0.0
    if lit:
        lit_penalty = 15 * lit.high_risk_count + 10 * lit.ibc_count + 2 * len(lit.cases)
    lit_score = clamp(100 - lit_penalty)
    explain.append(
        ExplainabilityScore(
            component="Litigation History",
            value=lit_score,
            reasoning=(
                f"{len(lit.cases)} case(s); {lit.high_risk_count} high-risk; {lit.ibc_count} IBC."
                if lit
                else "No litigation found."
            ),
            data_points={"total_value": lit.total_value if lit else 0.0},
            source_citations=["eCourts litigation search"],
        )
    )

    # Promoter background sub-score (25%).
    promoter = clamp(ctx.promoter_score)
    explain.append(
        ExplainabilityScore(
            component="Promoter Background",
            value=promoter,
            reasoning=f"Promoter background assessment score {promoter:.0f}/100.",
        )
    )

    # Fraud signal sub-score (20%) with up to 40-point penalty (Req 9.6).
    fraud_score = 95.0
    fraud_penalty = 0.0
    if ctx.fraud and ctx.fraud.findings:
        sev_weight = {
            Severity.CRITICAL: 40,
            Severity.HIGH: 25,
            Severity.MEDIUM: 12,
            Severity.LOW: 5,
        }
        fraud_penalty = min(40, sum(sev_weight.get(f.severity, 5) for f in ctx.fraud.findings))
        fraud_score = clamp(95 - fraud_penalty)
    explain.append(
        ExplainabilityScore(
            component="Fraud Signals",
            value=fraud_score,
            reasoning=(
                f"{len(ctx.fraud.findings)} fraud finding(s); penalty {fraud_penalty:.0f} pts."
                if ctx.fraud and ctx.fraud.findings
                else "No fraud signals detected."
            ),
            data_points={"penalty": fraud_penalty},
        )
    )

    base = 0.30 * mca_score + 0.25 * lit_score + 0.25 * promoter + 0.20 * fraud_score
    # Additional direct penalty so present fraud meaningfully reduces Character.
    final = clamp(base - 0.4 * fraud_penalty)
    return _dim(FiveCDimension.CHARACTER, final, explain)


def score_capacity(ctx: ScoringContext) -> DimensionScore:
    """Capacity (Req 10): DSCR, ICR, 3-yr revenue trend, EBITDA margin."""
    explain: list[ExplainabilityScore] = []
    fa = ctx.financials
    if not fa or not fa.latest:
        explain.append(
            ExplainabilityScore(
                component="Capacity",
                value=0.0,
                reasoning="No financial statements available to assess capacity.",
            )
        )
        return _dim(FiveCDimension.CAPACITY, 0.0, explain)

    latest = fa.latest
    d = ratios.dscr(latest)
    i = ratios.icr(latest)

    # DSCR sub-score: 1.0 -> 50, 1.25 -> 70, 2.0+ -> 100 (linear-ish).
    dscr_score = clamp(40 + (d - 1.0) * 60)
    flag_dscr = d < DSCR_MIN
    explain.append(
        ExplainabilityScore(
            component="DSCR",
            value=dscr_score,
            reasoning=(
                f"DSCR {d:.2f} is below minimum {DSCR_MIN}; inadequate debt servicing."
                if flag_dscr
                else f"DSCR {d:.2f} meets the {DSCR_MIN} threshold."
            ),
            data_points={"dscr": d, "threshold": DSCR_MIN},
            source_citations=[f"Financial Statement FY{latest.fiscal_year}"],
        )
    )

    icr_score = clamp(30 + i * 15)
    explain.append(
        ExplainabilityScore(
            component="ICR",
            value=icr_score,
            reasoning=f"Interest Coverage Ratio {i:.2f}x.",
            data_points={"icr": i},
        )
    )

    # 3-year revenue trend with temporal decay weights 50/30/20 (Req 10.4).
    revs = [s.profit_and_loss.revenue for s in fa.years_sorted_desc()[:3]]
    blended = three_year_trend_score(revs)
    growth = (
        safe_div(
            revs[0] - (revs[-1] if len(revs) > 1 else revs[0]), revs[-1] if len(revs) > 1 else 1.0
        )
        * 100
    )
    trend_score = clamp(55 + growth)
    explain.append(
        ExplainabilityScore(
            component="Revenue Trend (3yr, decay-weighted)",
            value=trend_score,
            reasoning=f"Decay-weighted revenue {blended:,.0f}; period growth {growth:+.1f}%.",
            data_points={"revenues_recent_first": revs, "weighted": blended},
        )
    )

    # EBITDA margin vs industry benchmark (Req 10.5).
    margin = latest.profit_and_loss.ebitda_margin
    margin_score = clamp(50 + (margin - ctx.industry_ebitda_margin) * 300)
    explain.append(
        ExplainabilityScore(
            component="EBITDA Margin",
            value=margin_score,
            reasoning=f"EBITDA margin {margin:.1%} vs industry {ctx.industry_ebitda_margin:.1%}.",
            data_points={
                "ebitda_margin": round(margin, 4),
                "benchmark": ctx.industry_ebitda_margin,
            },
        )
    )

    final = 0.35 * dscr_score + 0.20 * icr_score + 0.25 * trend_score + 0.20 * margin_score
    if flag_dscr:
        final = min(final, 45)
    return _dim(FiveCDimension.CAPACITY, final, explain)


def score_capital(ctx: ScoringContext) -> DimensionScore:
    """Capital (Req 11): D/E, current/quick ratio, net worth trend."""
    explain: list[ExplainabilityScore] = []
    fa = ctx.financials
    if not fa or not fa.latest:
        explain.append(
            ExplainabilityScore(
                component="Capital",
                value=0.0,
                reasoning="No financial statements available to assess capital.",
            )
        )
        return _dim(FiveCDimension.CAPITAL, 0.0, explain)

    latest = fa.latest
    de = ratios.debt_to_equity(latest)
    cr = ratios.current_ratio(latest)
    qr = ratios.quick_ratio(latest)

    # D/E sub-score with up to 30-pt penalty above 3.0 (Req 11.5).
    de_score = clamp(100 - de * 20)
    de_penalty = 0.0
    if de > DE_PENALTY_THRESHOLD:
        de_penalty = min(30, (de - DE_PENALTY_THRESHOLD) * 15 + 10)
    explain.append(
        ExplainabilityScore(
            component="Debt-to-Equity",
            value=de_score,
            reasoning=(
                f"D/E {de:.2f} exceeds {DE_PENALTY_THRESHOLD}; penalty {de_penalty:.0f} pts."
                if de_penalty
                else f"D/E {de:.2f} is within acceptable range."
            ),
            data_points={"debt_to_equity": de, "penalty": de_penalty},
            source_citations=[f"Balance Sheet FY{latest.fiscal_year}"],
        )
    )

    liq_score = clamp(40 + (cr - 1.0) * 30 + (qr - 1.0) * 15)
    explain.append(
        ExplainabilityScore(
            component="Liquidity (Current/Quick)",
            value=liq_score,
            reasoning=f"Current ratio {cr:.2f}, Quick ratio {qr:.2f}.",
            data_points={"current_ratio": cr, "quick_ratio": qr},
        )
    )

    # Net worth trend over up to 3 years (Req 11.4 / 11.6).
    nets = [s.balance_sheet.net_worth for s in fa.years_sorted_desc()[:3]]
    declining_2y = len(nets) >= 3 and nets[0] < nets[1] < nets[2]
    nw_score = 75.0
    if declining_2y:
        nw_score = 40.0
    if any(n < 0 for n in nets):
        nw_score = min(nw_score, 25.0)
    explain.append(
        ExplainabilityScore(
            component="Net Worth Trend",
            value=nw_score,
            reasoning=(
                "Net worth declined for 2 consecutive years; deteriorating capital."
                if declining_2y
                else "Net worth stable/improving."
            ),
            data_points={"net_worth_recent_first": nets},
        )
    )

    final = clamp(0.45 * de_score + 0.30 * liq_score + 0.25 * nw_score - 0.3 * de_penalty)
    return _dim(FiveCDimension.CAPITAL, final, explain)


def score_collateral(ctx: ScoringContext) -> DimensionScore:
    """Collateral (Req 12): LTV after type-specific haircuts."""
    explain: list[ExplainabilityScore] = []
    loan = ctx.loan_request
    if not loan or not loan.collateral:
        explain.append(
            ExplainabilityScore(
                component="Collateral",
                value=30.0,
                reasoning="No collateral provided; treated as largely unsecured.",
            )
        )
        return _dim(FiveCDimension.COLLATERAL, 30.0, explain)

    secured_value = 0.0
    breakdown = []
    for item in loan.collateral:
        haircut = COLLATERAL_HAIRCUTS.get(item.collateral_type.lower(), DEFAULT_HAIRCUT)
        net = item.market_value * (1 - haircut)
        secured_value += net
        breakdown.append(
            {
                "type": item.collateral_type,
                "market_value": item.market_value,
                "haircut": haircut,
                "net_value": round(net, 2),
            }
        )

    ltv = safe_div(loan.amount, secured_value, default=99.0)
    over_ltv = ltv > LTV_MAX
    # LTV 0 -> 100, 0.75 -> 60, 1.0 -> 40, >1 lower.
    score = clamp(100 - ltv * 60)
    if over_ltv:
        score = min(score, 45)
    explain.append(
        ExplainabilityScore(
            component="Loan-to-Value (post-haircut)",
            value=score,
            reasoning=(
                f"LTV {ltv:.0%} exceeds {LTV_MAX:.0%}; insufficient collateral coverage."
                if over_ltv
                else f"LTV {ltv:.0%} within {LTV_MAX:.0%} limit."
            ),
            data_points={
                "ltv": round(ltv, 4),
                "secured_value": round(secured_value, 2),
                "breakdown": breakdown,
            },
        )
    )
    return _dim(FiveCDimension.COLLATERAL, score, explain)


def score_conditions(ctx: ScoringContext) -> DimensionScore:
    """Conditions (Req 13): industry growth, regulatory, news sentiment."""
    explain: list[ExplainabilityScore] = []

    growth_score = clamp(50 + ctx.industry_growth_pct * 2.5)
    explain.append(
        ExplainabilityScore(
            component="Industry Growth",
            value=growth_score,
            reasoning=f"Sector growth trend {ctx.industry_growth_pct:.1f}%.",
            data_points={"industry_growth_pct": ctx.industry_growth_pct},
        )
    )

    reg_score = clamp(100 - ctx.regulatory_risk * 100)
    explain.append(
        ExplainabilityScore(
            component="Regulatory Environment",
            value=reg_score,
            reasoning=f"Regulatory risk index {ctx.regulatory_risk:.2f}.",
            data_points={"regulatory_risk": ctx.regulatory_risk},
        )
    )

    # News sentiment over last 90 days (Req 13.4/13.5).
    recent = [r for r in ctx.research if r.recency in ("HIGH", "MEDIUM")]
    adverse = sum(1 for r in recent if r.sentiment == Sentiment.ADVERSE)
    adverse_ratio = safe_div(adverse, len(recent), default=0.0)
    news_score = 70.0
    news_penalty = 0.0
    if adverse_ratio > NEGATIVE_NEWS_THRESHOLD:
        news_penalty = min(25, (adverse_ratio - NEGATIVE_NEWS_THRESHOLD) * 100 + 10)
        news_score = clamp(70 - news_penalty)
    explain.append(
        ExplainabilityScore(
            component="News Sentiment",
            value=news_score,
            reasoning=(
                f"Adverse news {adverse_ratio:.0%} exceeds {NEGATIVE_NEWS_THRESHOLD:.0%}; "
                f"penalty {news_penalty:.0f} pts."
                if news_penalty
                else f"Adverse news ratio {adverse_ratio:.0%}."
            ),
            data_points={"adverse_ratio": round(adverse_ratio, 2), "sample": len(recent)},
        )
    )

    final = clamp(0.40 * growth_score + 0.30 * reg_score + 0.30 * news_score - 0.3 * news_penalty)
    return _dim(FiveCDimension.CONDITIONS, final, explain)


def evaluate_qualitative_note(note: QualitativeNote) -> float:
    """Compute a note's score impact in the range -15 to +8 (Requirement 26.4)."""
    sev_factor = {
        Severity.CRITICAL: 1.0,
        Severity.HIGH: 0.75,
        Severity.MEDIUM: 0.5,
        Severity.LOW: 0.25,
        Severity.INFO: 0.1,
    }.get(note.severity, 0.25)
    if note.sentiment == Sentiment.ADVERSE:
        impact = -15.0 * sev_factor
    elif note.sentiment == Sentiment.POSITIVE:
        impact = 8.0 * sev_factor
    else:
        impact = 0.0
    return round(max(-15.0, min(8.0, impact)), 2)


def apply_qualitative_notes(
    dimensions: list[DimensionScore], notes: list[QualitativeNote]
) -> list[QualitativeNote]:
    """Apply qualitative note adjustments to matching dimensions (Req 26)."""
    by_dim = {d.dimension: d for d in dimensions}
    for note in notes:
        if note.score_impact == 0.0:
            note.score_impact = evaluate_qualitative_note(note)
        target = by_dim.get(note.dimension) if note.dimension else None
        if target is None:
            continue
        new_score = clamp(target.score + note.score_impact)
        target.explainability.append(
            ExplainabilityScore(
                component=f"Qualitative Note ({note.officer_id})",
                value=note.score_impact,
                reasoning=f"Officer observation ({note.sentiment.value}/{note.severity.value}): "
                f"{note.text[:120]}",
                data_points={"impact": note.score_impact, "prev_score": target.score},
            )
        )
        target.score = round(new_score, 2)
        target.is_critical_weakness = target.score < CRITICAL_WEAKNESS_FLOOR
    return notes


def compute_data_completeness(ctx: ScoringContext) -> float:
    """Fraction of expected analysis inputs that are present (Req 14.5/24.4)."""
    checks = [
        ctx.financials is not None and bool(ctx.financials.statements),
        ctx.gst is not None and bool(ctx.gst.returns),
        ctx.bank is not None and bool(ctx.bank.transactions),
        ctx.compliance is not None,
        ctx.litigation is not None,
        ctx.loan_request is not None,
    ]
    return round(sum(1 for c in checks if c) / len(checks), 4)


def recommend(
    overall: float, critical: list[FiveCDimension], fraud: Optional[FraudReport]
) -> Recommendation:
    """Derive a final recommendation (Requirement 15.9)."""
    if fraud and fraud.has_critical:
        return Recommendation.REJECT
    if overall >= 65 and not critical:
        return Recommendation.APPROVE
    if overall >= 50 and len(critical) <= 1:
        return Recommendation.APPROVE_WITH_CONDITIONS
    return Recommendation.REJECT


def synthesize(
    ctx: ScoringContext,
    notes: Optional[list[QualitativeNote]] = None,
    model: Optional["ScoreModel"] = None,
) -> CreditScore:
    """Compute the overall credit score from all Five Cs (Requirement 14).

    By default the overall score is the transparent weighted sum. Passing a
    trained `ScoreModel` (Requirement 25) computes the overall score from the
    dimensions + context features instead, keeping the heuristic as the default.
    """
    dimensions = [
        score_character(ctx),
        score_capacity(ctx),
        score_capital(ctx),
        score_collateral(ctx),
        score_conditions(ctx),
    ]
    if notes:
        apply_qualitative_notes(dimensions, notes)

    if model is not None:
        features = {
            "industry_growth_pct": ctx.industry_growth_pct,
            "industry_ebitda_margin": ctx.industry_ebitda_margin,
            "promoter_score": ctx.promoter_score,
            "regulatory_risk": ctx.regulatory_risk,
        }
        overall = round(clamp(model.predict_overall(dimensions, features)), 2)
    else:
        overall = round(sum(d.score * d.weight for d in dimensions), 2)
    band = score_to_risk_band(overall)
    critical = [d.dimension for d in dimensions if d.is_critical_weakness]

    completeness = compute_data_completeness(ctx)
    # Confidence interval widens as completeness drops (Req 14.5).
    margin = round((1 - completeness) * 20 + 3, 2)
    low_conf = completeness < LOW_CONFIDENCE_THRESHOLD

    score = CreditScore(
        overall_score=overall,
        risk_band=band,
        dimensions=dimensions,
        data_completeness=completeness,
        confidence_low=round(clamp(overall - margin), 2),
        confidence_high=round(clamp(overall + margin), 2),
        low_confidence=low_conf,
        critical_weaknesses=critical,
        recommendation=recommend(overall, critical, ctx.fraud),
        model_version=ctx.model_version,
    )
    logger.info(
        "Credit score: %.1f (%s) band=%s critical=%d completeness=%.2f",
        overall,
        score.recommendation.value,
        band.value,
        len(critical),
        completeness,
    )
    return score
