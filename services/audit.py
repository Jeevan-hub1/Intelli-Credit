"""Audit logging service with 7-year retention (Requirement 19.4/19.5)."""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from sqlalchemy.orm import Session

from models.base import utcnow
from models.db_models import DBAuditLog
from utils.logging import get_logger

logger = get_logger(__name__)

RETENTION_YEARS = 7
RETENTION_DAYS = RETENTION_YEARS * 365


def record_audit(
    db: Session,
    *,
    user_id: Optional[str],
    action: str,
    resource_type: str = "",
    resource_id: str = "",
    success: bool = True,
    detail: Optional[dict] = None,
) -> DBAuditLog:
    """Persist an audit log entry with a 7-year retention deadline."""
    now = utcnow()
    entry = DBAuditLog(
        user_id=user_id or "",
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        success=success,
        detail=detail or {},
        retain_until=now + timedelta(days=RETENTION_DAYS),
        created_at=now,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    logger.info("Audit: action=%s user=%s resource=%s/%s success=%s",
                action, user_id, resource_type, resource_id, success)
    return entry


def record_access_denied(db: Session, *, user_id: Optional[str], action: str,
                         resource_id: str = "", reason: str = "") -> DBAuditLog:
    """Log an unauthorized access attempt (Requirement 19.3)."""
    return record_audit(db, user_id=user_id, action=f"DENIED:{action}",
                        resource_id=resource_id, success=False,
                        detail={"reason": reason})
