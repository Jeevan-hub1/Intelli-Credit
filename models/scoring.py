"""Scoring, fraud, compliance, and research domain models."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import Field

from models.base import (
    ExplainabilityScore,
    FiveCDimension,
    IntelliBaseModel,
    Recommendation,
    RiskBand,
    Sentiment,
    Severity,
    utcnow,
)

# ----------------------------- Fraud --------------------------------------


class TransactionCycle(IntelliBaseModel):
    """A detected circular-trading cycle (Requirement 5)."""

    entity_chain: list[str] = Field(default_factory=list, description="GSTIN/entity chain")
    total_value: float = 0.0
    span_days: int = 0
    edges: list[dict] = Field(default_factory=list, description="from/to/amount/date edges")


class FraudFinding(IntelliBaseModel):
    """A fraud-detection finding (Requirements 5, 6)."""

    kind: str = Field(..., description="circular_trading | fake_itc | itc_mismatch")
    severity: Severity = Severity.MEDIUM
    description: str
    metrics: dict = Field(default_factory=dict)
    cycles: list[TransactionCycle] = Field(default_factory=list)


class FraudReport(IntelliBaseModel):
    """Aggregated fraud detection output."""

    findings: list[FraudFinding] = Field(default_factory=list)
    circular_trading_ratio: float = 0.0
    itc_to_revenue_ratio: float = 0.0
    has_critical: bool = False


# -------------------- Compliance / litigation / research -------------------


class ComplianceCheck(IntelliBaseModel):
    """MCA21 compliance verification result (Requirement 7)."""

    cin: Optional[str] = None
    overdue_filings: list[str] = Field(default_factory=list)
    disqualified_directors: list[str] = Field(default_factory=list)
    is_compliant: bool = True
    violations: list[str] = Field(default_factory=list)


class LitigationCase(IntelliBaseModel):
    """A single litigation case (Requirement 8)."""

    case_id: str
    category: str = Field(..., description="civil | criminal | tax | labor | insolvency")
    title: str = ""
    status: str = "pending"
    monetary_value: float = 0.0
    is_high_risk: bool = False
    is_ibc: bool = False


class LitigationReport(IntelliBaseModel):
    """Aggregated litigation search result."""

    cases: list[LitigationCase] = Field(default_factory=list)
    total_value: float = 0.0
    high_risk_count: int = 0
    ibc_count: int = 0


class ResearchFinding(IntelliBaseModel):
    """Autonomous research agent finding (Requirement 28)."""

    title: str
    summary: str
    url: str
    source_authority: str = Field(
        ..., description="government | rating_agency | news | social_media"
    )
    recency: str = Field(..., description="HIGH | MEDIUM | LOW")
    sentiment: Sentiment = Sentiment.NEUTRAL
    retrieved_at: datetime = Field(default_factory=utcnow)
    rank_score: float = 0.0


# ------------------------- Qualitative notes -------------------------------


class QualitativeNote(IntelliBaseModel):
    """Credit officer qualitative observation (Requirement 26)."""

    id: Optional[str] = None
    officer_id: str
    text: str
    dimension: Optional[FiveCDimension] = None
    sentiment: Sentiment = Sentiment.NEUTRAL
    severity: Severity = Severity.LOW
    score_impact: float = Field(default=0.0, description="-15 to +8 points")
    created_at: datetime = Field(default_factory=utcnow)


# --------------------------- Credit scores ---------------------------------


class DimensionScore(IntelliBaseModel):
    """Score for a single C of the Five Cs."""

    dimension: FiveCDimension
    score: float = Field(default=0.0, ge=0.0, le=100.0)
    weight: float = 0.0
    is_critical_weakness: bool = False
    explainability: list[ExplainabilityScore] = Field(default_factory=list)
    overridden: bool = False
    override_reason: Optional[str] = None
    override_by: Optional[str] = None


class CreditScore(IntelliBaseModel):
    """Overall credit score synthesis (Requirement 14)."""

    overall_score: float = Field(default=0.0, ge=0.0, le=100.0)
    risk_band: RiskBand = RiskBand.HIGH_RISK
    dimensions: list[DimensionScore] = Field(default_factory=list)
    data_completeness: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_low: float = 0.0
    confidence_high: float = 0.0
    low_confidence: bool = False
    critical_weaknesses: list[FiveCDimension] = Field(default_factory=list)
    recommendation: Recommendation = Recommendation.REJECT
    model_version: Optional[str] = None
    computed_at: datetime = Field(default_factory=utcnow)

    def dimension(self, dim: FiveCDimension) -> Optional[DimensionScore]:
        for d in self.dimensions:
            if d.dimension == dim:
                return d
        return None
