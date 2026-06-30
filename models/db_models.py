"""SQLAlchemy ORM models for persistence and audit trails.

These cover metadata, users/RBAC, audit logs (7-year retention, Req 19.5),
model versioning (Req 25), and qualitative notes (Req 26).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from config.database import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DBUser(Base):
    """User account with role (Requirement 19.2)."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[str] = mapped_column(String(32), default="Analyst")
    password_hash: Mapped[str] = mapped_column(String(255), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)



class DBApplication(Base):
    """Application metadata (full domain payload stored as JSON)."""

    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    borrower_name: Mapped[str] = mapped_column(String(255), default="")
    cin: Mapped[str] = mapped_column(String(32), default="", index=True)
    industry: Mapped[str] = mapped_column(String(128), default="", index=True)
    status: Mapped[str] = mapped_column(String(32), default="created")
    loan_amount: Mapped[float] = mapped_column(Float, default=0.0)
    assigned_officer_id: Mapped[str] = mapped_column(String(64), default="")
    model_version: Mapped[str] = mapped_column(String(32), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    documents: Mapped[list["DBDocument"]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )


class DBDocument(Base):
    """Document metadata (Requirement 1)."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    filename: Mapped[str] = mapped_column(String(512), default="")
    doc_type: Mapped[str] = mapped_column(String(64), default="unknown")
    storage_key: Mapped[str] = mapped_column(String(256), default="")
    classification_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    needs_manual_review: Mapped[bool] = mapped_column(Boolean, default=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    application: Mapped["DBApplication"] = relationship(back_populates="documents")


class DBCreditScore(Base):
    """Persisted overall credit score per application."""

    __tablename__ = "credit_scores"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    overall_score: Mapped[float] = mapped_column(Float, default=0.0)
    risk_band: Mapped[str] = mapped_column(String(32), default="")
    model_version: Mapped[str] = mapped_column(String(32), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)



class DBCAM(Base):
    """Persisted CAM versions (Requirement 30: full version history)."""

    __tablename__ = "cams"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    recommendation: Mapped[str] = mapped_column(String(32), default="")
    overall_score: Mapped[float] = mapped_column(Float, default=0.0)
    is_final: Mapped[bool] = mapped_column(Boolean, default=False)
    model_version: Mapped[str] = mapped_column(String(32), default="")
    modification_reason: Mapped[str] = mapped_column(String(512), default="")
    generated_by: Mapped[str] = mapped_column(String(64), default="")
    pdf_key: Mapped[str] = mapped_column(String(256), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class DBAuditLog(Base):
    """Audit trail with 7-year retention (Requirement 19.4/19.5)."""

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    action: Mapped[str] = mapped_column(String(128), default="", index=True)
    resource_type: Mapped[str] = mapped_column(String(64), default="")
    resource_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    retain_until: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class DBModelVersion(Base):
    """Model version registry (Requirement 25)."""

    __tablename__ = "model_versions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    version: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    changelog: Mapped[str] = mapped_column(Text, default="")
    approval_rate: Mapped[float] = mapped_column(Float, default=0.0)
    default_rate: Mapped[float] = mapped_column(Float, default=0.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class DBQualitativeNote(Base):
    """Credit officer qualitative observations (Requirement 26)."""

    __tablename__ = "qualitative_notes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    officer_id: Mapped[str] = mapped_column(String(64), default="")
    dimension: Mapped[str] = mapped_column(String(32), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    sentiment: Mapped[str] = mapped_column(String(16), default="Neutral")
    severity: Mapped[str] = mapped_column(String(16), default="Low")
    score_impact: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)



class DBOverride(Base):
    """Manual score override audit (Requirement 21.4)."""

    __tablename__ = "score_overrides"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(64), default="")
    dimension: Mapped[str] = mapped_column(String(32), default="")
    old_score: Mapped[float] = mapped_column(Float, default=0.0)
    new_score: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class DBEWSAlert(Base):
    """Persisted EWS alerts (Requirements 17, 18)."""

    __tablename__ = "ews_alerts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    borrower_id: Mapped[str] = mapped_column(String(64), index=True)
    application_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    severity: Mapped[str] = mapped_column(String(16), default="Low")
    ews_score: Mapped[float] = mapped_column(Float, default=0.0)
    exposure_amount: Mapped[float] = mapped_column(Float, default=0.0)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    assigned_officer_id: Mapped[str] = mapped_column(String(64), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
