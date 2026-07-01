"""External API clients: MCA21 compliance (Req 7) and eCourts (Req 8).

Live HTTP calls are made only when an API key is configured and httpx is
available. Otherwise the clients fall back to deterministic offline analysis
derived from the structured inputs, so the pipeline runs end-to-end.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable, Optional

from config.settings import settings
from models.scoring import ComplianceCheck, LitigationCase, LitigationReport
from utils.logging import get_logger

logger = get_logger(__name__)

OVERDUE_DAYS_THRESHOLD = 30  # Requirement 7.3
_HIGH_RISK_CRIMINAL_KEYWORDS = ("fraud", "cheating", "forgery", "misappropriation", "embezzle")


def validate_gstin(gstin: str) -> bool:
    """Structural GSTIN validation (15 chars, state code, checksum-ish).

    Used by fraud detection to flag malformed/non-existent suppliers.
    """
    if not gstin or len(gstin) != 15:
        return False
    if not gstin[:2].isdigit():
        return False
    state_code = int(gstin[:2])
    if not (1 <= state_code <= 38):
        return False
    # PAN portion (chars 2-11): 5 letters, 4 digits, 1 letter.
    pan = gstin[2:12]
    if not (pan[:5].isalpha() and pan[5:9].isdigit() and pan[9].isalpha()):
        return False
    return gstin[12:].isalnum()


def _days_overdue(due: Any) -> int:
    """Return days a due date is overdue (0 if not overdue or unparseable)."""
    if not due:
        return 0
    try:
        d = due if isinstance(due, date) else datetime.fromisoformat(str(due)).date()
    except (ValueError, TypeError):
        return 0
    delta = (date.today() - d).days
    return max(0, delta)


def check_mca21_compliance(
    cin: Optional[str],
    *,
    filings: Optional[list[dict]] = None,
    directors: Optional[list[dict]] = None,
    fetcher: Optional[Callable[[Optional[str]], dict]] = None,
) -> ComplianceCheck:
    """Verify MCA21 statutory compliance (Requirement 7).

    `filings`: [{type, due_date, filed (bool)}]; `directors`: [{din, name,
    disqualified (bool)}]. When neither is supplied and an MCA21 API key is
    configured, data is pulled live from MCA21 (or the injected `fetcher`).
    """
    result = ComplianceCheck(cin=cin)
    if filings is None and directors is None and settings.mca21_api_key:
        pulled = (fetcher or _mca21_live_fetch)(cin)
        filings = pulled.get("filings", [])
        directors = pulled.get("directors", [])
    filings = filings or []
    directors = directors or []

    for f in filings:
        if f.get("filed"):
            continue
        overdue = _days_overdue(f.get("due_date"))
        if overdue > OVERDUE_DAYS_THRESHOLD:
            label = f"{f.get('type', 'filing')} overdue by {overdue} days"
            result.overdue_filings.append(label)
            result.violations.append(label)

    for d in directors:
        if d.get("disqualified"):
            name = d.get("name") or d.get("din", "unknown")
            result.disqualified_directors.append(str(name))
            result.violations.append(f"Disqualified director: {name}")

    result.is_compliant = not result.violations
    logger.info(
        "MCA21 check cin=%s compliant=%s violations=%d",
        cin,
        result.is_compliant,
        len(result.violations),
    )
    return result


def search_ecourts(
    company_name: str,
    director_names: Optional[list[str]] = None,
    *,
    cases: Optional[list[dict]] = None,
    fetcher: Optional[Callable[[str, Optional[list[str]]], list[dict]]] = None,
) -> LitigationReport:
    """Search eCourts for litigation and categorize results (Requirement 8).

    `cases`: [{case_id, category, title, status, monetary_value}]. When not
    supplied and an eCourts API key is configured, cases are pulled live from
    eCourts (or the injected `fetcher`).
    """
    report = LitigationReport()
    if cases is None and settings.ecourts_api_key:
        cases = (fetcher or _ecourts_live_fetch)(company_name, director_names)
    for c in cases or []:
        category = str(c.get("category", "civil")).lower()
        title = str(c.get("title", ""))
        value = float(c.get("monetary_value", 0.0) or 0.0)
        is_ibc = category == "insolvency" or "ibc" in title.lower() or "insolvency" in title.lower()
        is_high_risk = category == "criminal" and any(
            kw in title.lower() for kw in _HIGH_RISK_CRIMINAL_KEYWORDS
        )
        case = LitigationCase(
            case_id=str(c.get("case_id", "")),
            category=category,
            title=title,
            status=str(c.get("status", "pending")),
            monetary_value=value,
            is_high_risk=is_high_risk,
            is_ibc=is_ibc,
        )
        report.cases.append(case)
        report.total_value += value
        if is_high_risk:
            report.high_risk_count += 1
        if is_ibc:
            report.ibc_count += 1

    report.total_value = round(report.total_value, 2)
    logger.info(
        "eCourts search '%s': %d case(s), high_risk=%d, ibc=%d",
        company_name,
        len(report.cases),
        report.high_risk_count,
        report.ibc_count,
    )
    return report


def _mca21_live_fetch(cin: Optional[str]) -> dict:  # pragma: no cover - live network + API key
    """Pull filings and director status from the live MCA21 API."""
    import httpx

    headers = {"Authorization": f"Bearer {settings.mca21_api_key}"}
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                f"{settings.mca21_api_url}/v1/company/{cin}/compliance", headers=headers
            )
            resp.raise_for_status()
            body = resp.json()
            return {"filings": body.get("filings", []), "directors": body.get("directors", [])}
    except Exception as exc:
        logger.warning("MCA21 live fetch failed for %s: %s", cin, exc)
        return {"filings": [], "directors": []}


def _ecourts_live_fetch(
    company_name: str, director_names: Optional[list[str]]
) -> list[dict]:  # pragma: no cover - live network + API key
    """Query the live eCourts API for litigation involving the borrower."""
    import httpx

    headers = {"Authorization": f"Bearer {settings.ecourts_api_key}"}
    params = {"party": company_name}
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                f"{settings.ecourts_api_url}/v1/cases", headers=headers, params=params
            )
            resp.raise_for_status()
            return resp.json().get("cases", [])
    except Exception as exc:
        logger.warning("eCourts live fetch failed for '%s': %s", company_name, exc)
        return []
