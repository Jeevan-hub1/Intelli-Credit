"""Early Warning System domain models (Requirements 17, 18)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import Field

from models.base import IntelliBaseModel, Severity, utcnow


class TriggerEvent(IntelliBaseModel):
    """A monitored EWS trigger event (Requirement 17.1)."""

    kind: str = Field(..., description="delayed_financials | gst_irregular | adverse_news | rating_downgrade")
    description: str
    severity: Severity = Severity.MEDIUM
    occurred_at: datetime = Field(default_factory=utcnow)
    weight: float = 1.0


class EWSAlert(IntelliBaseModel):
    """A prioritized EWS alert (Requirement 18)."""

    id: Optional[str] = None
    borrower_id: str
    application_id: Optional[str] = None
    severity: Severity = Severity.LOW
    triggers: list[TriggerEvent] = Field(default_factory=list)
    ews_score: float = Field(default=0.0, ge=0.0, le=100.0)
    exposure_amount: float = 0.0
    escalated: bool = False
    assigned_officer_id: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)


class EWSDigestEntry(IntelliBaseModel):
    """A ranked entry in the daily high-risk digest (Requirement 18.4)."""

    borrower_id: str
    ews_score: float
    exposure_amount: float
    severity: Severity
    rank: int
