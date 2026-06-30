"""GST return parsing and reconciliation service (Requirement 3)."""
from __future__ import annotations

from typing import Any

from models.base import Flag, Severity
from models.gst import (
    GSTData,
    GSTLineEntry,
    GSTReturn,
    ITCReconciliation,
    RevenueTrendPoint,
)
from utils.logging import get_logger
from utils.numeric import pct_change, safe_div

logger = get_logger(__name__)

ITC_MISMATCH_THRESHOLD_PCT = 5.0   # Requirement 3.3


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_return(raw: dict[str, Any]) -> GSTReturn:
    """Parse a single GSTR-2A/3B return payload into the schema."""
    entries = [
        GSTLineEntry(
            gstin=str(e.get("gstin", "")),
            counterparty_name=e.get("counterparty_name"),
            taxable_value=_num(e.get("taxable_value")),
            igst=_num(e.get("igst")),
            cgst=_num(e.get("cgst")),
            sgst=_num(e.get("sgst")),
            invoice_count=int(e.get("invoice_count", 0) or 0),
        )
        for e in raw.get("entries", [])
    ]
    return GSTReturn(
        form_type=str(raw.get("form_type", "GSTR-3B")),
        period=str(raw.get("period", "")),
        gstin=str(raw.get("gstin", "")),
        total_taxable_value=_num(raw.get("total_taxable_value")),
        total_itc=_num(raw.get("total_itc")),
        total_tax_liability=_num(raw.get("total_tax_liability")),
        entries=entries,
    )



def _reconcile_itc(returns: list[GSTReturn]) -> list[ITCReconciliation]:
    """Reconcile ITC between GSTR-2A and GSTR-3B per period (Req 3.2/3.3)."""
    by_period: dict[str, dict[str, GSTReturn]] = {}
    for r in returns:
        by_period.setdefault(r.period, {})[r.form_type.upper()] = r

    recons: list[ITCReconciliation] = []
    for period, forms in by_period.items():
        r2a = forms.get("GSTR-2A")
        r3b = forms.get("GSTR-3B")
        if not (r2a and r3b):
            continue
        itc_2a = r2a.total_itc
        itc_3b = r3b.total_itc
        variance = itc_3b - itc_2a
        # Variance as a percentage of the 2A baseline (eligible credit).
        variance_pct = abs(safe_div(variance, itc_2a, default=0.0)) * 100.0
        recons.append(
            ITCReconciliation(
                period=period,
                itc_2a=itc_2a,
                itc_3b=itc_3b,
                variance_amount=variance,
                variance_pct=round(variance_pct, 2),
                flagged=variance_pct > ITC_MISMATCH_THRESHOLD_PCT,
            )
        )
    return recons


def _revenue_trend(returns: list[GSTReturn]) -> list[RevenueTrendPoint]:
    """Month-over-month revenue trend from GSTR-3B data (Req 3.5)."""
    gstr3b = sorted(
        [r for r in returns if r.form_type.upper() == "GSTR-3B"],
        key=lambda r: r.period,
    )
    points: list[RevenueTrendPoint] = []
    prev: float | None = None
    for r in gstr3b:
        rev = r.total_taxable_value
        points.append(
            RevenueTrendPoint(
                period=r.period,
                revenue=rev,
                mom_change_pct=round(pct_change(rev, prev), 2) if prev is not None else None,
            )
        )
        prev = rev
    return points



def parse_gst(raw_returns: list[dict[str, Any]], *, gstin: str = "") -> GSTData:
    """Parse GST returns and produce reconciliation + trend analysis."""
    returns = [parse_return(r) for r in raw_returns if r]
    primary_gstin = gstin or (returns[0].gstin if returns else "")

    # Supplier GSTINs come from GSTR-2A entries; customers from GSTR-1/3B B2B.
    suppliers: set[str] = set()
    customers: set[str] = set()
    for r in returns:
        for e in r.entries:
            if not e.gstin:
                continue
            if r.form_type.upper() == "GSTR-2A":
                suppliers.add(e.gstin)
            else:
                customers.add(e.gstin)

    recons = _reconcile_itc(returns)
    trend = _revenue_trend(returns)

    total_revenue = sum(r.total_taxable_value for r in returns if r.form_type.upper() == "GSTR-3B")
    total_itc = sum(r.total_itc for r in returns if r.form_type.upper() == "GSTR-3B")
    itc_ratio = round(safe_div(total_itc, total_revenue, default=0.0), 4)

    data = GSTData(
        gstin=primary_gstin,
        returns=returns,
        reconciliations=recons,
        revenue_trend=trend,
        supplier_gstins=sorted(suppliers),
        customer_gstins=sorted(customers),
        itc_to_revenue_ratio=itc_ratio,
    )

    # Requirement 3.3: flag material ITC mismatches with calculated variance.
    for rec in recons:
        if rec.flagged:
            data.flags.append(
                Flag(
                    code="ITC_MISMATCH",
                    message=(
                        f"Period {rec.period}: ITC variance {rec.variance_pct:.1f}% "
                        f"exceeds {ITC_MISMATCH_THRESHOLD_PCT:.0f}% (2A {rec.itc_2a:,.0f} "
                        f"vs 3B {rec.itc_3b:,.0f})."
                    ),
                    severity=Severity.HIGH,
                    context={"variance_amount": rec.variance_amount},
                )
            )
    logger.info("Parsed %d GST return(s); %d reconciliation period(s)", len(returns), len(recons))
    return data
