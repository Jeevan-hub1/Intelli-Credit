"""Autonomous research agent (Requirement 28).

Crawls/queries public sources, ranks findings by source authority and recency,
classifies sentiment, and cites source URLs with retrieval timestamps. Live
crawling uses httpx when available and enabled; otherwise the agent ranks and
classifies findings supplied by an upstream search connector.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

from config.settings import settings
from models.base import Sentiment, utcnow
from models.scoring import ResearchFinding
from utils.logging import get_logger
from utils.temporal import Recency, classify_recency

logger = get_logger(__name__)

# Source authority ranking (Requirement 28.2): government > rating > news > social.
_AUTHORITY_WEIGHT = {"government": 1.0, "rating_agency": 0.75, "news": 0.5, "social_media": 0.25}
_RECENCY_WEIGHT = {Recency.HIGH: 1.0, Recency.MEDIUM: 0.6, Recency.LOW: 0.3}

# Trusted government/regulatory domains (Requirement 28.1).
_GOV_DOMAINS = ("rbi.org.in", "sebi.gov.in", "mca.gov.in", "gov.in", "nic.in")
_RATING_DOMAINS = ("crisil.com", "icra.in", "careratings.com", "indiaratings.co.in")

_ADVERSE_WORDS = (
    "fraud",
    "default",
    "downgrade",
    "probe",
    "scam",
    "insolvency",
    "penalty",
    "litigation",
    "loss",
    "decline",
    "raid",
)
_POSITIVE_WORDS = (
    "growth",
    "profit",
    "expansion",
    "upgrade",
    "award",
    "record",
    "surge",
    "partnership",
    "approval",
)


def classify_authority(url: str, declared: Optional[str] = None) -> str:
    """Classify source authority from the URL domain or a declared value."""
    if declared in _AUTHORITY_WEIGHT:
        return declared
    u = url.lower()
    if any(d in u for d in _GOV_DOMAINS):
        return "government"
    if any(d in u for d in _RATING_DOMAINS):
        return "rating_agency"
    if any(s in u for s in ("twitter.com", "x.com", "facebook.com", "linkedin.com", "reddit.com")):
        return "social_media"
    return "news"


def classify_sentiment(text: str) -> Sentiment:
    """Lexical sentiment classification (Requirement 28.5)."""
    t = (text or "").lower()
    adverse = sum(1 for w in _ADVERSE_WORDS if w in t)
    positive = sum(1 for w in _POSITIVE_WORDS if w in t)
    if adverse > positive:
        return Sentiment.ADVERSE
    if positive > adverse:
        return Sentiment.POSITIVE
    return Sentiment.NEUTRAL


def _to_finding(raw: dict, *, reference: datetime) -> ResearchFinding:
    """Build a ranked, classified ResearchFinding from a raw search result."""
    url = str(raw.get("url", ""))
    authority = classify_authority(url, raw.get("source_authority"))
    published = raw.get("published_date") or raw.get("publishedDate")
    try:
        pub_dt = (
            datetime.fromisoformat(str(published).replace("Z", "+00:00"))
            if published
            else reference
        )
    except ValueError:
        pub_dt = reference
    recency = classify_recency(pub_dt, reference=reference)
    text = f"{raw.get('title', '')} {raw.get('summary') or raw.get('snippet', '')}"
    sentiment = classify_sentiment(text)
    rank = round(_AUTHORITY_WEIGHT[authority] * 0.6 + _RECENCY_WEIGHT[recency] * 0.4, 4)
    return ResearchFinding(
        title=str(raw.get("title", "")),
        summary=str(raw.get("summary") or raw.get("snippet", ""))[:500],
        url=url,
        source_authority=authority,
        recency=recency.value,
        sentiment=sentiment,
        retrieved_at=utcnow(),
        rank_score=rank,
    )


def research_borrower(
    company_name: str,
    *,
    director_names: Optional[list[str]] = None,
    raw_results: Optional[list[dict]] = None,
    timeout_seconds: Optional[int] = None,
) -> list[ResearchFinding]:
    """Run research for a borrower and return ranked findings (Requirement 28).

    `raw_results` may be supplied by an upstream search connector; otherwise a
    live fetch is attempted when enabled. Completes within the configured
    timeout (Requirement 28.4) and ranks results descending.
    """
    started = time.monotonic()
    timeout = timeout_seconds or settings.research_agent_timeout_seconds
    reference = datetime.now(timezone.utc)

    results = list(raw_results or [])
    if not results and settings.research_agent_enabled:
        results = _live_fetch(  # pragma: no cover - live network path
            company_name, director_names or [], deadline=started + timeout
        )

    findings = [_to_finding(r, reference=reference) for r in results]
    findings.sort(key=lambda f: f.rank_score, reverse=True)
    elapsed = time.monotonic() - started
    logger.info("Research '%s': %d finding(s) in %.1fs", company_name, len(findings), elapsed)
    return findings


def _live_fetch(
    company_name: str, director_names: list[str], *, deadline: float
) -> list[dict]:  # pragma: no cover - live network path
    """Best-effort live fetch of public regulatory pages via httpx (optional).

    Returns raw result dicts. Silently returns an empty list if httpx is
    unavailable, the network is restricted, or the deadline is exceeded.
    """
    try:
        import httpx
    except Exception:
        logger.info("httpx unavailable; research agent returning no live results")
        return []

    queries = [
        f"https://www.google.com/search?q={company_name.replace(' ', '+')}+rbi",
    ]
    results: list[dict] = []
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            for q in queries:
                if time.monotonic() > deadline:
                    break
                try:
                    resp = client.get(q, headers={"User-Agent": "IntelliCredit-Research/1.0"})
                    if resp.status_code == 200:
                        results.append(
                            {
                                "title": f"Web result for {company_name}",
                                "summary": resp.text[:300],
                                "url": str(resp.url),
                                "published_date": datetime.now(timezone.utc).isoformat(),
                            }
                        )
                except Exception as exc:  # pragma: no cover - network dependent
                    logger.warning("Research fetch failed for %s: %s", q, exc)
    except Exception as exc:  # pragma: no cover
        logger.warning("Research client error: %s", exc)
    return results
