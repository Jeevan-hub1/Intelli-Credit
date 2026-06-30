"""Pydantic request/response schemas for the REST API."""
from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from models.base import FiveCDimension, Sentiment, Severity
from services.external_apis import validate_gstin

_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


class BorrowerIn(BaseModel):
    name: str
    cin: Optional[str] = None
    gstin: Optional[str] = None
    pan: Optional[str] = None
    industry: str = "unclassified"
    business_description: Optional[str] = None
    director_names: list[str] = Field(default_factory=list)

    @field_validator("gstin")
    @classmethod
    def _check_gstin(cls, v: Optional[str]) -> Optional[str]:
        if v and not validate_gstin(v):
            raise ValueError("Invalid GSTIN format (expected 15-char GSTIN)")
        return v

    @field_validator("pan")
    @classmethod
    def _check_pan(cls, v: Optional[str]) -> Optional[str]:
        if v and not _PAN_RE.match(v):
            raise ValueError("Invalid PAN format (expected AAAAA9999A)")
        return v


class CollateralIn(BaseModel):
    description: str
    collateral_type: str
    market_value: float = Field(ge=0)


class LoanRequestIn(BaseModel):
    amount: float = Field(gt=0)
    tenure_months: int = Field(gt=0)
    purpose: Optional[str] = None
    collateral: list[CollateralIn] = Field(default_factory=list)


class ApplicationCreate(BaseModel):
    borrower: BorrowerIn
    loan_request: LoanRequestIn


class AnalyzeRequest(BaseModel):
    raw_financials: list[dict[str, Any]] = Field(default_factory=list)
    raw_gst_returns: list[dict[str, Any]] = Field(default_factory=list)
    raw_bank_statement: Optional[dict[str, Any]] = None
    mca21_filings: list[dict[str, Any]] = Field(default_factory=list)
    mca21_directors: list[dict[str, Any]] = Field(default_factory=list)
    litigation_cases: list[dict[str, Any]] = Field(default_factory=list)
    research_results: list[dict[str, Any]] = Field(default_factory=list)
    extra_payment_edges: list[dict[str, Any]] = Field(default_factory=list)
    industry_growth_pct: float = 8.0
    industry_ebitda_margin: float = 0.12
    promoter_score: float = 70.0
    regulatory_risk: float = 0.2



class QualitativeNoteIn(BaseModel):
    text: str
    dimension: Optional[FiveCDimension] = None
    sentiment: Sentiment = Sentiment.NEUTRAL
    severity: Severity = Severity.LOW


class OverrideIn(BaseModel):
    dimension: FiveCDimension
    new_score: float = Field(ge=0, le=100)
    reason: str = Field(min_length=3)


class FinalizeIn(BaseModel):
    approve: bool = True
    reason: Optional[str] = None


class EWSCheckIn(BaseModel):
    borrower_id: str
    application_id: Optional[str] = None
    exposure_amount: float = 0.0
    assigned_officer_id: Optional[str] = None
    days_financials_overdue: int = 0
    gst_filing_gaps: int = 0
    adverse_news_count: int = 0
    rating_downgraded: bool = False


class PortfolioLoanIn(BaseModel):
    borrower_id: str
    borrower_name: str
    industry: str
    exposure: float
    days_past_due: int = 0
    overall_score: float = 0.0


class ReportRequest(BaseModel):
    period: str
    capital_base: float = 0.0
    loans: list[PortfolioLoanIn] = Field(default_factory=list)
