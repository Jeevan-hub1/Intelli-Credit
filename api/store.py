"""Persistence helpers bridging domain objects and the database.

Summaries are persisted to the database for durability and audit; the full
in-memory analysis artifacts (scoring context, CAM versions) are kept in a
process registry to support fast review/override workflows.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from models.application import Application
from models.cam import CAM
from models.db_models import DBApplication, DBCAM, DBCreditScore
from models.scoring import CreditScore
from services.credit_engine import AnalysisResult


class _Registry:
    """In-process cache of live analysis artifacts keyed by application id."""

    def __init__(self) -> None:
        self.results: dict[str, AnalysisResult] = {}
        self.cam_versions: dict[str, list[CAM]] = {}

    def put_result(self, result: AnalysisResult) -> None:
        app_id = result.application.id
        self.results[app_id] = result
        self.cam_versions.setdefault(app_id, []).append(result.cam)

    def add_cam_version(self, app_id: str, cam: CAM) -> None:
        self.cam_versions.setdefault(app_id, []).append(cam)


registry = _Registry()


def persist_application(db: Session, app: Application) -> DBApplication:
    """Insert or update the application metadata row."""
    row = db.get(DBApplication, app.id)
    payload = app.model_dump(mode="json")
    if row is None:
        row = DBApplication(id=app.id)
        db.add(row)
    row.borrower_name = app.borrower.name
    row.cin = app.borrower.cin or ""
    row.industry = app.borrower.industry
    row.status = app.status.value
    row.loan_amount = app.loan_request.amount
    row.assigned_officer_id = app.assigned_officer_id or ""
    row.model_version = app.model_version or ""
    row.payload = payload
    db.commit()
    db.refresh(row)
    return row



def persist_credit_score(db: Session, app_id: str, score: CreditScore) -> None:
    """Persist a credit score snapshot."""
    row = DBCreditScore(
        application_id=app_id,
        overall_score=score.overall_score,
        risk_band=score.risk_band.value,
        model_version=score.model_version or "",
        payload=score.model_dump(mode="json"),
    )
    db.add(row)
    db.commit()


def persist_cam(db: Session, cam: CAM) -> None:
    """Persist a CAM version (Requirement 30: full version history retained)."""
    row = db.get(DBCAM, cam.id)
    if row is None:
        row = DBCAM(id=cam.id)
        db.add(row)
    row.application_id = cam.application_id
    row.version = cam.version
    row.recommendation = cam.recommendation.value
    row.overall_score = cam.overall_score
    row.is_final = cam.is_final
    row.model_version = cam.model_version or ""
    row.modification_reason = cam.modification_reason or ""
    row.generated_by = cam.generated_by or ""
    row.pdf_key = cam.pdf_key or ""
    row.payload = cam.model_dump(mode="json")
    db.commit()


def load_result(app_id: str) -> Optional[AnalysisResult]:
    """Return the live in-memory analysis result (needed for override/regeneration)."""
    return registry.results.get(app_id)


def load_credit_score(db: Session, app_id: str) -> Optional[CreditScore]:
    """Return the credit score from the registry, falling back to the database.

    This makes read endpoints durable across restarts and multi-worker
    deployments where the in-process registry may be empty.
    """
    live = registry.results.get(app_id)
    if live is not None:
        return live.credit_score
    row = (
        db.query(DBCreditScore)
        .filter(DBCreditScore.application_id == app_id)
        .order_by(DBCreditScore.created_at.desc())
        .first()
    )
    return CreditScore.model_validate(row.payload) if row else None


def list_cam_versions(app_id: str, db: Optional[Session] = None) -> list[CAM]:
    """List CAM versions from the registry, falling back to the database."""
    live = registry.cam_versions.get(app_id)
    if live:
        return live
    if db is None:
        return []
    rows = (
        db.query(DBCAM)
        .filter(DBCAM.application_id == app_id)
        .order_by(DBCAM.version)
        .all()
    )
    return [CAM.model_validate(r.payload) for r in rows]
