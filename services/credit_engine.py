"""Credit engine orchestrator (Requirements 14, 20, 24, 27).

Coordinates the full pipeline: parse -> fraud -> external checks -> score ->
CAM, while persisting results, writing the Delta feature store, and emitting
audit logs. Designed to complete within the 5-minute SLA (Requirement 20.1).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from models.application import Application
from models.base import ApplicationStatus
from models.scoring import CreditScore, QualitativeNote
from services import (
    bank_parser,
    cam_generator,
    external_apis,
    financial_parser,
    fraud_detector,
    gst_parser,
    scoring,
)
from services.feature_store import feature_store
from services.research_agent import research_borrower
from utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class AnalysisRequest:
    """Raw structured inputs for a full credit analysis."""

    application: Application
    raw_financials: list[dict] = field(default_factory=list)
    raw_gst_returns: list[dict] = field(default_factory=list)
    raw_bank_statement: Optional[dict] = None
    mca21_filings: list[dict] = field(default_factory=list)
    mca21_directors: list[dict] = field(default_factory=list)
    litigation_cases: list[dict] = field(default_factory=list)
    research_results: list[dict] = field(default_factory=list)
    extra_payment_edges: list[dict] = field(default_factory=list)
    qualitative_notes: list[QualitativeNote] = field(default_factory=list)
    industry_growth_pct: float = 8.0
    industry_ebitda_margin: float = 0.12
    promoter_score: float = 70.0
    regulatory_risk: float = 0.2


@dataclass
class AnalysisResult:
    """Full analysis output bundle."""

    application: Application
    credit_score: CreditScore
    cam: Any
    scoring_context: scoring.ScoringContext
    elapsed_seconds: float
    feature_version: int


def analyze(
    req: AnalysisRequest,
    *,
    model_version: str = "1.0.0",
    generated_by: Optional[str] = None,
    persist_features: bool = True,
) -> AnalysisResult:
    """Run the end-to-end credit analysis pipeline for an application.

    `persist_features=False` skips the feature-store write, used when
    rehydrating an analysis in memory from a persisted snapshot.
    """
    started = time.monotonic()
    app = req.application
    app.status = ApplicationStatus.ANALYZING
    app.model_version = model_version
    borrower = app.borrower

    # 1. Parse documents.
    financials = (
        financial_parser.parse_financials(req.raw_financials) if req.raw_financials else None
    )
    gst = (
        gst_parser.parse_gst(req.raw_gst_returns, gstin=borrower.gstin or "")
        if req.raw_gst_returns
        else None
    )
    bank = (
        bank_parser.parse_bank_statement(req.raw_bank_statement) if req.raw_bank_statement else None
    )

    # 2. External checks.
    compliance = external_apis.check_mca21_compliance(
        borrower.cin, filings=req.mca21_filings, directors=req.mca21_directors
    )
    litigation = external_apis.search_ecourts(
        borrower.name, borrower.director_names, cases=req.litigation_cases
    )
    research = research_borrower(
        borrower.name, director_names=borrower.director_names, raw_results=req.research_results
    )

    # 3. Fraud detection.
    total_revenue = (
        financials.latest.profit_and_loss.revenue if (financials and financials.latest) else 0.0
    )
    fraud = fraud_detector.detect_fraud(
        bank=bank,
        gst=gst,
        self_entity=borrower.gstin or borrower.name,
        total_revenue=total_revenue,
        extra_edges=req.extra_payment_edges,
        gstin_validator=external_apis.validate_gstin,
    )

    # 4. Scoring.
    ctx = scoring.ScoringContext(
        loan_request=app.loan_request,
        financials=financials,
        gst=gst,
        bank=bank,
        fraud=fraud,
        compliance=compliance,
        litigation=litigation,
        research=research,
        industry_growth_pct=req.industry_growth_pct,
        industry_ebitda_margin=req.industry_ebitda_margin,
        promoter_score=req.promoter_score,
        regulatory_risk=req.regulatory_risk,
        model_version=model_version,
    )
    credit_score = scoring.synthesize(ctx, notes=req.qualitative_notes)

    # 5. CAM generation.
    cam = cam_generator.generate_cam(
        cam_generator.CAMInputs(
            application=app,
            credit_score=credit_score,
            financials=financials,
            gst=gst,
            bank=bank,
            fraud=fraud,
            compliance=compliance,
            litigation=litigation,
            research=research,
            qualitative_notes=req.qualitative_notes,
        ),
        generated_by=generated_by,
    )

    # 6. Persist features to the Delta feature store (Req 27).
    feature_version = 0
    if persist_features:
        feature_version = feature_store.write_features(
            application_id=app.id,
            features=_extract_features(credit_score, fraud, financials, gst, bank),
            application_date=app.created_at.date().isoformat(),
            borrower_industry=borrower.industry,
            source="credit_engine",
        )

    app.status = ApplicationStatus.ANALYSIS_COMPLETE
    elapsed = time.monotonic() - started
    logger.info(
        "Analysis complete app=%s score=%.1f in %.2fs", app.id, credit_score.overall_score, elapsed
    )
    return AnalysisResult(
        application=app,
        credit_score=credit_score,
        cam=cam,
        scoring_context=ctx,
        elapsed_seconds=round(elapsed, 3),
        feature_version=feature_version,
    )


def _extract_features(credit_score, fraud, financials, gst, bank) -> dict:
    """Flatten key model outputs into a feature dict for the feature store."""
    feats: dict[str, Any] = {
        "overall_score": credit_score.overall_score,
        "risk_band": credit_score.risk_band.value,
        "recommendation": credit_score.recommendation.value,
        "data_completeness": credit_score.data_completeness,
        "circular_trading_ratio": fraud.circular_trading_ratio if fraud else 0.0,
        "has_critical_fraud": fraud.has_critical if fraud else False,
    }
    for d in credit_score.dimensions:
        feats[f"score_{d.dimension.value.lower()}"] = d.score
    if financials and financials.latest:
        feats["latest_revenue"] = financials.latest.profit_and_loss.revenue
    if gst:
        feats["itc_to_revenue_ratio"] = gst.itc_to_revenue_ratio
    if bank:
        feats["monthly_avg_balance"] = bank.conduct.monthly_average_balance
    return feats
