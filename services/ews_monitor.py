"""Early Warning System monitoring and alert prioritization (Req 17, 18)."""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

from models.base import Severity, utcnow
from models.ews import EWSAlert, EWSDigestEntry, TriggerEvent
from utils.logging import get_logger
from utils.numeric import clamp

logger = get_logger(__name__)

# Severity weights feeding the EWS score (Requirement 17.7).
_SEVERITY_WEIGHT = {
    Severity.CRITICAL: 40.0,
    Severity.HIGH: 25.0,
    Severity.MEDIUM: 12.0,
    Severity.LOW: 5.0,
    Severity.INFO: 1.0,
}
_SEVERITY_ORDER = [Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
ESCALATION_WINDOW_DAYS = 30  # Requirement 18.2


def compute_ews_score(triggers: list[TriggerEvent]) -> float:
    """EWS score 0-100 from number and severity of trigger events (Req 17.7)."""
    if not triggers:
        return 0.0
    raw = sum(_SEVERITY_WEIGHT.get(t.severity, 5.0) * t.weight for t in triggers)
    return round(clamp(raw), 2)


def _base_severity(score: float) -> Severity:
    if score >= 70:
        return Severity.CRITICAL
    if score >= 45:
        return Severity.HIGH
    if score >= 20:
        return Severity.MEDIUM
    return Severity.LOW


def _escalate(severity: Severity) -> Severity:
    idx = _SEVERITY_ORDER.index(severity)
    return _SEVERITY_ORDER[min(idx + 1, len(_SEVERITY_ORDER) - 1)]


def evaluate_alert(
    borrower_id: str,
    triggers: list[TriggerEvent],
    *,
    application_id: Optional[str] = None,
    exposure_amount: float = 0.0,
    assigned_officer_id: Optional[str] = None,
) -> EWSAlert:
    """Build a prioritized EWS alert from trigger events (Req 17, 18).

    Severity escalates one level when multiple triggers occur for the same
    borrower within the 30-day window (Requirement 18.2).
    """
    score = compute_ews_score(triggers)
    severity = _base_severity(score)

    now = utcnow()
    recent = [
        t for t in triggers if (now - t.occurred_at) <= timedelta(days=ESCALATION_WINDOW_DAYS)
    ]
    escalated = False
    if len(recent) > 1:
        severity = _escalate(severity)
        escalated = True

    alert = EWSAlert(
        borrower_id=borrower_id,
        application_id=application_id,
        severity=severity,
        triggers=triggers,
        ews_score=score,
        exposure_amount=exposure_amount,
        escalated=escalated,
        assigned_officer_id=assigned_officer_id,
    )
    if severity == Severity.CRITICAL:
        _notify_officer(alert)
    logger.info(
        "EWS alert borrower=%s score=%.1f severity=%s escalated=%s",
        borrower_id,
        score,
        severity.value,
        escalated,
    )
    return alert


def _notify_officer(alert: EWSAlert) -> None:
    """Send immediate notification for a Critical alert (Req 18.5).

    Hook point for email/SMS/webhook integrations; logs in this build.
    """
    logger.warning(
        "CRITICAL EWS alert for borrower %s (score %.1f) -> officer %s",
        alert.borrower_id,
        alert.ews_score,
        alert.assigned_officer_id or "unassigned",
        extra={"context": {"notification": "critical_ews", "borrower_id": alert.borrower_id}},
    )


def daily_digest(alerts: list[EWSAlert], *, top_n: int = 10) -> list[EWSDigestEntry]:
    """Rank borrowers by EWS score then exposure; return top N (Req 18.3/18.4)."""
    ranked = sorted(alerts, key=lambda a: (a.ews_score, a.exposure_amount), reverse=True)
    digest: list[EWSDigestEntry] = []
    for rank, alert in enumerate(ranked[:top_n], start=1):
        digest.append(
            EWSDigestEntry(
                borrower_id=alert.borrower_id,
                ews_score=alert.ews_score,
                exposure_amount=alert.exposure_amount,
                severity=alert.severity,
                rank=rank,
            )
        )
    return digest


def detect_triggers(
    *,
    days_financials_overdue: int = 0,
    gst_filing_gaps: int = 0,
    adverse_news_count: int = 0,
    rating_downgraded: bool = False,
) -> list[TriggerEvent]:
    """Translate monitoring signals into trigger events (Req 17.2-17.5)."""
    triggers: list[TriggerEvent] = []
    if days_financials_overdue > 0:
        sev = Severity.HIGH if days_financials_overdue > 60 else Severity.MEDIUM
        triggers.append(
            TriggerEvent(
                kind="delayed_financials",
                description=f"Financial statements overdue by {days_financials_overdue} days.",
                severity=sev,
            )
        )
    if gst_filing_gaps > 0:
        triggers.append(
            TriggerEvent(
                kind="gst_irregular",
                description=f"{gst_filing_gaps} GST filing gap(s) detected.",
                severity=Severity.MEDIUM,
                weight=float(gst_filing_gaps),
            )
        )
    if adverse_news_count > 0:
        sev = Severity.HIGH if adverse_news_count >= 3 else Severity.MEDIUM
        triggers.append(
            TriggerEvent(
                kind="adverse_news",
                description=f"{adverse_news_count} adverse news mention(s).",
                severity=sev,
            )
        )
    if rating_downgraded:
        triggers.append(
            TriggerEvent(
                kind="rating_downgrade",
                description="Credit rating downgrade reported by rating agency.",
                severity=Severity.HIGH,
            )
        )
    return triggers
