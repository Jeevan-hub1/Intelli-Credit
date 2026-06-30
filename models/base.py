"""Base models and shared enumerations."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    """Timezone-aware current UTC timestamp."""
    return datetime.now(timezone.utc)


class IntelliBaseModel(BaseModel):
    """Base for all domain models with permissive config."""

    model_config = ConfigDict(use_enum_values=False, populate_by_name=True)


class UserRole(str, Enum):
    """RBAC roles (Requirement 19.2)."""

    ANALYST = "Analyst"
    CREDIT_OFFICER = "Credit_Officer"
    ADMINISTRATOR = "Administrator"


class DocumentType(str, Enum):
    """Supported document classifications (Requirement 1.2)."""

    ANNUAL_REPORT = "annual_report"
    FINANCIAL_STATEMENT = "financial_statement"
    GST_RETURN = "gst_return"
    BANK_STATEMENT = "bank_statement"
    LEGAL_NOTICE = "legal_notice"
    RATING_REPORT = "rating_report"
    UNKNOWN = "unknown"


class DocumentFormat(str, Enum):
    """Supported upload formats (Requirement 1.1)."""

    PDF = "pdf"
    EXCEL = "excel"
    CSV = "csv"
    IMAGE = "image"


class AccountingStandard(str, Enum):
    """Financial reporting standard (Requirements 2.2/2.3)."""

    INDIAN_GAAP = "indian_gaap"
    IND_AS = "ind_as"
    UNKNOWN = "unknown"



class ApplicationStatus(str, Enum):
    """Lifecycle states of a credit application."""

    CREATED = "created"
    DOCUMENTS_UPLOADED = "documents_uploaded"
    ANALYZING = "analyzing"
    ANALYSIS_COMPLETE = "analysis_complete"
    UNDER_REVIEW = "under_review"
    CAM_FINALIZED = "cam_finalized"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"


class FiveCDimension(str, Enum):
    """The Five Cs of credit (Requirement glossary)."""

    CHARACTER = "Character"
    CAPACITY = "Capacity"
    CAPITAL = "Capital"
    COLLATERAL = "Collateral"
    CONDITIONS = "Conditions"


# Five Cs weights in the overall score (Requirement glossary / Req 9-13).
FIVE_C_WEIGHTS: dict[FiveCDimension, float] = {
    FiveCDimension.CHARACTER: 0.20,
    FiveCDimension.CAPACITY: 0.30,
    FiveCDimension.CAPITAL: 0.20,
    FiveCDimension.COLLATERAL: 0.15,
    FiveCDimension.CONDITIONS: 0.15,
}


class RiskBand(str, Enum):
    """Overall credit risk bands (Requirement 14.2)."""

    EXCELLENT = "Excellent"   # 80-100
    GOOD = "Good"             # 65-79
    FAIR = "Fair"             # 50-64
    POOR = "Poor"             # 35-49
    HIGH_RISK = "High Risk"   # 0-34


class Severity(str, Enum):
    """Generic severity scale for alerts and findings."""

    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    INFO = "Info"


class Sentiment(str, Enum):
    """Sentiment classification (Requirements 13.4, 28.5)."""

    POSITIVE = "Positive"
    NEUTRAL = "Neutral"
    ADVERSE = "Adverse"


class Recommendation(str, Enum):
    """Final CAM recommendation (Requirement 15.9)."""

    APPROVE = "Approve"
    APPROVE_WITH_CONDITIONS = "Approve with Conditions"
    REJECT = "Reject"



def score_to_risk_band(score: float) -> RiskBand:
    """Map a 0-100 credit score to a risk band (Requirement 14.2)."""
    if score >= 80:
        return RiskBand.EXCELLENT
    if score >= 65:
        return RiskBand.GOOD
    if score >= 50:
        return RiskBand.FAIR
    if score >= 35:
        return RiskBand.POOR
    return RiskBand.HIGH_RISK


class ExplainabilityScore(IntelliBaseModel):
    """Human-readable reasoning attached to a score or finding.

    Implements the Explainability_Score concept referenced throughout the
    Five Cs requirements (e.g. 9.7, 10.7, 11.7, 12.6, 13.6, 16.x).
    """

    component: str = Field(..., description="Name of the scored component")
    value: float = Field(..., description="Numeric contribution / score value")
    reasoning: str = Field(..., description="Plain-language explanation")
    data_points: dict = Field(default_factory=dict, description="Calculations used")
    source_citations: list[str] = Field(
        default_factory=list, description="Source documents, e.g. 'Balance Sheet FY2023, Page 5'"
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class Flag(IntelliBaseModel):
    """A data-quality or risk flag raised during analysis."""

    code: str
    message: str
    severity: Severity = Severity.MEDIUM
    context: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
