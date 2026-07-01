"""Unit tests for numeric helpers and the research agent (Req 28)."""

from models.base import Sentiment
from services.research_agent import classify_authority, classify_sentiment, research_borrower
from utils.numeric import cagr, clamp, pct_change, safe_div


def test_safe_div_and_clamp():
    assert safe_div(10, 2) == 5
    assert safe_div(10, 0, default=-1) == -1
    assert clamp(150) == 100
    assert clamp(-5) == 0
    assert clamp(50) == 50


def test_pct_change_and_cagr():
    assert pct_change(110, 100) == 10.0
    assert pct_change(100, 0) is None
    assert cagr(100, 200, 0) is None
    assert cagr(0, 100, 3) is None
    assert round(cagr(100, 133.1, 3), 1) == 10.0


def test_classify_authority():
    assert classify_authority("https://rbi.org.in/notice") == "government"
    assert classify_authority("https://crisil.com/rating") == "rating_agency"
    assert classify_authority("https://twitter.com/x") == "social_media"
    assert classify_authority("https://somenews.com/a") == "news"


def test_classify_sentiment():
    assert classify_sentiment("massive fraud and default probe") == Sentiment.ADVERSE
    assert classify_sentiment("record profit and expansion") == Sentiment.POSITIVE
    assert classify_sentiment("the company held a meeting") == Sentiment.NEUTRAL


def test_research_ranking_authority_and_recency():
    findings = research_borrower(
        "Acme Ltd",
        raw_results=[
            {
                "title": "RBI penalty",
                "snippet": "fraud probe",
                "url": "https://rbi.org.in/x",
                "published_date": "2026-06-20T00:00:00+00:00",
            },
            {
                "title": "growth",
                "snippet": "record profit",
                "url": "https://news.example.com/a",
                "published_date": "2024-01-01T00:00:00+00:00",
            },
        ],
    )
    # Government + recent ranks first; news + old ranks last.
    assert findings[0].source_authority == "government"
    assert findings[0].sentiment == Sentiment.ADVERSE
    assert findings[0].rank_score >= findings[-1].rank_score
