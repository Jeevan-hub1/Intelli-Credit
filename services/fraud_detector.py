"""Fraud detection service (Requirements 5 & 6).

Implements circular-trading detection via transaction-graph cycle search and
fake-ITC detection via supplier-GSTIN verification and ratio benchmarking.
A pure-Python cycle finder is used by default; networkx is used if installed.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable, Optional

from models.bank import BankStatement
from models.base import Severity
from models.gst import GSTData
from models.scoring import FraudFinding, FraudReport, TransactionCycle
from utils.logging import get_logger
from utils.numeric import safe_div

logger = get_logger(__name__)

CYCLE_WINDOW_DAYS = 90                 # Requirement 5.2
HIGH_RISK_CYCLE_VALUE = 1_000_000      # INR 10 lakhs (Requirement 5.3)
CIRCULAR_RATIO_CRITICAL = 0.15         # Requirement 5.5
SINGLE_TXN_SUPPLIER_THRESHOLD = 500_000   # INR 5 lakhs (Requirement 6.5)
ITC_DEVIATION_THRESHOLD = 0.30         # Requirement 6.4
MAX_CYCLE_LENGTH = 8                   # bound DFS depth for tractability


class Edge:
    """A directed payment edge between two entities."""

    __slots__ = ("src", "dst", "amount", "txn_date")

    def __init__(self, src: str, dst: str, amount: float, txn_date: date) -> None:
        self.src = src
        self.dst = dst
        self.amount = amount
        self.txn_date = txn_date

    def as_dict(self) -> dict:
        return {
            "from": self.src,
            "to": self.dst,
            "amount": self.amount,
            "date": self.txn_date.isoformat(),
        }



def build_transaction_graph(
    bank: Optional[BankStatement],
    *,
    self_entity: str,
    extra_edges: Optional[Iterable[dict]] = None,
) -> list[Edge]:
    """Construct directed payment edges from bank + supplemental data (Req 5.1).

    Each transaction with a resolved counterparty becomes an edge: a debit is
    a payment from self_entity to the counterparty; a credit is the reverse.
    """
    edges: list[Edge] = []
    if bank:
        for t in bank.transactions:
            cp = t.counterparty
            if not cp:
                continue
            if t.debit > 0:
                edges.append(Edge(self_entity, cp, t.debit, t.txn_date))
            if t.credit > 0:
                edges.append(Edge(cp, self_entity, t.credit, t.txn_date))
    for e in extra_edges or []:
        edges.append(
            Edge(
                str(e["from"]),
                str(e["to"]),
                float(e.get("amount", 0.0)),
                e["date"] if isinstance(e["date"], date) else date.fromisoformat(str(e["date"])),
            )
        )
    return edges


def _find_cycles(edges: list[Edge]) -> list[list[Edge]]:
    """Find directed cycles where funds return to origin within the window.

    Bounded DFS over the multigraph; a cycle is accepted only if every edge
    in it falls within CYCLE_WINDOW_DAYS of the others (Requirement 5.2).
    """
    adj: dict[str, list[Edge]] = {}
    for e in edges:
        adj.setdefault(e.src, []).append(e)

    cycles: list[list[Edge]] = []
    seen_signatures: set[tuple] = set()

    def dfs(start: str, current: str, path: list[Edge], visited: set[str]) -> None:
        if len(path) > MAX_CYCLE_LENGTH:
            return
        for edge in adj.get(current, []):
            if edge.dst == start and path:
                cycle = path + [edge]
                dates = [c.txn_date for c in cycle]
                if (max(dates) - min(dates)).days <= CYCLE_WINDOW_DAYS:
                    nodes = tuple(sorted({c.src for c in cycle} | {c.dst for c in cycle}))
                    sig = (nodes, round(sum(c.amount for c in cycle), 2))
                    if sig not in seen_signatures:
                        seen_signatures.add(sig)
                        cycles.append(cycle)
            elif edge.dst not in visited:
                dfs(start, edge.dst, path + [edge], visited | {edge.dst})

    for node in list(adj.keys()):
        dfs(node, node, [], {node})
    return cycles



def detect_circular_trading(
    edges: list[Edge],
    *,
    total_revenue: float,
) -> tuple[list[FraudFinding], float]:
    """Detect circular trading and compute the circular trading ratio (Req 5)."""
    findings: list[FraudFinding] = []
    raw_cycles = _find_cycles(edges)
    circular_value = 0.0

    for cyc in raw_cycles:
        total_value = round(sum(c.amount for c in cyc), 2)
        # Circular value contribution: the limiting (minimum) flow around loop.
        loop_value = min(c.amount for c in cyc)
        circular_value += loop_value
        chain = [cyc[0].src] + [c.dst for c in cyc]
        span = (max(c.txn_date for c in cyc) - min(c.txn_date for c in cyc)).days
        cycle_model = TransactionCycle(
            entity_chain=chain,
            total_value=total_value,
            span_days=span,
            edges=[c.as_dict() for c in cyc],
        )
        if total_value > HIGH_RISK_CYCLE_VALUE:
            findings.append(
                FraudFinding(
                    kind="circular_trading",
                    severity=Severity.HIGH,
                    description=(
                        f"Circular trading cycle of INR {total_value:,.0f} across "
                        f"{len(chain) - 1} hops returning to origin within {span} days."
                    ),
                    metrics={"total_value": total_value, "span_days": span, "hops": len(chain) - 1},
                    cycles=[cycle_model],
                )
            )

    ratio = round(safe_div(circular_value, total_revenue, default=0.0), 4)
    if ratio > CIRCULAR_RATIO_CRITICAL:
        chains = [f.cycles[0].entity_chain for f in findings if f.cycles]
        findings.append(
            FraudFinding(
                kind="circular_trading",
                severity=Severity.CRITICAL,
                description=(
                    f"Circular trading ratio {ratio:.1%} exceeds {CIRCULAR_RATIO_CRITICAL:.0%}; "
                    f"likely revenue inflation."
                ),
                metrics={"circular_trading_ratio": ratio, "entity_chains": chains},
            )
        )
    return findings, ratio



def detect_fake_itc(
    gst: Optional[GSTData],
    *,
    industry_median_itc_ratio: float = 0.12,
    gstin_validator=None,
) -> list[FraudFinding]:
    """Detect fake ITC: invalid suppliers, ratio deviation, single-txn spikes (Req 6)."""
    findings: list[FraudFinding] = []
    if not gst:
        return findings

    # Req 6.1/6.2: verify supplier GSTINs; flag inactive/non-existent.
    if gstin_validator is not None:
        suspicious = [g for g in gst.supplier_gstins if not gstin_validator(g)]
        if suspicious:
            findings.append(
                FraudFinding(
                    kind="fake_itc",
                    severity=Severity.HIGH,
                    description=f"{len(suspicious)} supplier GSTIN(s) inactive/non-existent.",
                    metrics={"suspicious_gstins": suspicious},
                )
            )

    # Req 6.3/6.4: ITC-to-revenue ratio vs industry median.
    deviation = safe_div(
        gst.itc_to_revenue_ratio - industry_median_itc_ratio,
        industry_median_itc_ratio,
        default=0.0,
    )
    if abs(deviation) > ITC_DEVIATION_THRESHOLD:
        findings.append(
            FraudFinding(
                kind="fake_itc",
                severity=Severity.MEDIUM,
                description=(
                    f"ITC-to-revenue ratio {gst.itc_to_revenue_ratio:.1%} deviates "
                    f"{deviation:+.0%} from industry median {industry_median_itc_ratio:.1%}."
                ),
                metrics={"itc_ratio": gst.itc_to_revenue_ratio, "deviation": round(deviation, 4)},
            )
        )

    # Req 6.5: single-transaction supplier relationships exceeding INR 5 lakhs.
    big_single: list[str] = []
    for r in gst.returns:
        if r.form_type.upper() != "GSTR-2A":
            continue
        for e in r.entries:
            if e.invoice_count == 1 and e.taxable_value > SINGLE_TXN_SUPPLIER_THRESHOLD:
                big_single.append(e.gstin)
    if big_single:
        findings.append(
            FraudFinding(
                kind="fake_itc",
                severity=Severity.MEDIUM,
                description=f"{len(big_single)} supplier(s) with single transactions over INR 5 lakhs.",
                metrics={"suppliers": sorted(set(big_single))},
            )
        )
    return findings



def detect_fraud(
    *,
    bank: Optional[BankStatement] = None,
    gst: Optional[GSTData] = None,
    self_entity: str = "SELF",
    total_revenue: float = 0.0,
    extra_edges: Optional[Iterable[dict]] = None,
    industry_median_itc_ratio: float = 0.12,
    gstin_validator=None,
) -> FraudReport:
    """Run the full fraud-detection suite and aggregate a FraudReport."""
    edges = build_transaction_graph(bank, self_entity=self_entity, extra_edges=extra_edges)
    circular_findings, ratio = detect_circular_trading(edges, total_revenue=total_revenue)
    itc_findings = detect_fake_itc(
        gst,
        industry_median_itc_ratio=industry_median_itc_ratio,
        gstin_validator=gstin_validator,
    )
    findings = circular_findings + itc_findings
    report = FraudReport(
        findings=findings,
        circular_trading_ratio=ratio,
        itc_to_revenue_ratio=gst.itc_to_revenue_ratio if gst else 0.0,
        has_critical=any(f.severity == Severity.CRITICAL for f in findings),
    )
    logger.info(
        "Fraud scan: %d finding(s), circular_ratio=%.3f, critical=%s",
        len(findings), ratio, report.has_critical,
    )
    return report
