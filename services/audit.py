"""Audit logging service with 7-year retention (Requirement 19.4/19.5).

Entries are hash-chained (each entry's hash incorporates the previous entry's
hash) so any tampering or deletion in the trail becomes detectable.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.base import utcnow
from models.db_models import DBAuditLog
from utils.logging import get_logger

logger = get_logger(__name__)

RETENTION_YEARS = 7
RETENTION_DAYS = RETENTION_YEARS * 365

# Serialize the read-latest-then-append so the hash chain cannot fork under
# concurrent writers within a process (a distributed deployment would use a
# DB sequence or a serialized outbox instead).
_chain_lock = threading.Lock()


def _ts(dt) -> str:
    """Normalize a timestamp to a naive-UTC ISO string for stable hashing.

    SQLite drops tzinfo on round-trip, so we normalize both at write and
    verification time to keep the chained hash deterministic across storage.
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat()


def _entry_hash(prev_hash: str, fields: dict) -> str:
    """Compute the chained SHA-256 hash for an audit entry."""
    payload = json.dumps(fields, sort_keys=True, default=str)
    return hashlib.sha256(f"{prev_hash}:{payload}".encode()).hexdigest()


def _latest_hash(db: Session) -> str:
    """Return the entry_hash of the most recent audit row (chain head)."""
    row = db.execute(select(DBAuditLog).order_by(DBAuditLog.seq.desc()).limit(1)).scalars().first()
    return row.entry_hash if row else ""


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
    """Persist a hash-chained audit entry with a 7-year retention deadline."""
    now = utcnow()
    with _chain_lock:
        prev_hash = _latest_hash(db)
        fields = {
            "user_id": user_id or "",
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "success": success,
            "detail": detail or {},
            "created_at": _ts(now),
        }
        entry = DBAuditLog(
            user_id=user_id or "",
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            success=success,
            detail=detail or {},
            prev_hash=prev_hash,
            entry_hash=_entry_hash(prev_hash, fields),
            retain_until=now + timedelta(days=RETENTION_DAYS),
            created_at=now,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
    logger.info(
        "Audit: action=%s user=%s resource=%s/%s success=%s",
        action,
        user_id,
        resource_type,
        resource_id,
        success,
    )
    return entry


def verify_chain(db: Session) -> bool:
    """Recompute the hash chain and return True if it is intact (Req 19.5)."""
    rows = db.execute(select(DBAuditLog).order_by(DBAuditLog.seq.asc())).scalars().all()
    prev = ""
    for row in rows:
        fields = {
            "user_id": row.user_id,
            "action": row.action,
            "resource_type": row.resource_type,
            "resource_id": row.resource_id,
            "success": row.success,
            "detail": row.detail,
            "created_at": _ts(row.created_at),
        }
        if row.prev_hash != prev or row.entry_hash != _entry_hash(prev, fields):
            return False
        prev = row.entry_hash
    return True


def record_access_denied(
    db: Session, *, user_id: Optional[str], action: str, resource_id: str = "", reason: str = ""
) -> DBAuditLog:
    """Log an unauthorized access attempt (Requirement 19.3)."""
    return record_audit(
        db,
        user_id=user_id,
        action=f"DENIED:{action}",
        resource_id=resource_id,
        success=False,
        detail={"reason": reason},
    )
