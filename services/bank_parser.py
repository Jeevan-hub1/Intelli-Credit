"""Bank statement parsing service (Requirement 4)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from models.bank import BankStatement, ConductMetrics, Transaction
from models.base import Flag, Severity
from utils.logging import get_logger

logger = get_logger(__name__)

CATEGORY_CONFIDENCE_THRESHOLD = 0.80  # Requirement 4.3

# Keyword -> (category, base confidence) rules for categorization (Req 4.2).
_CATEGORY_RULES: list[tuple[tuple[str, ...], str, float]] = [
    (("salary", "payroll", "wages"), "salary", 0.95),
    (("emi", "loan repayment", "loan instalment", "installment"), "loan_emi", 0.92),
    (("gst", "gstr", "tax payment", "tds"), "gst_payment", 0.9),
    (("neft", "rtgs", "imps", "vendor", "supplier", "purchase"), "vendor_payment", 0.82),
    (("interest", "int paid", "int credit"), "interest", 0.88),
    (("atm", "cash wdl", "cash withdrawal"), "cash_withdrawal", 0.9),
    (("rev", "sales", "receipt", "collection"), "revenue_receipt", 0.8),
    (("chrg", "charges", "fee", "penalty"), "bank_charges", 0.85),
]

_BOUNCE_KEYWORDS = ("return", "bounce", "insufficient", "rtn", "ecs return", "chq rtn", "dishonour")


def _to_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(str(value), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date: {value!r}")


def categorize(description: str) -> tuple[str, float]:
    """Categorize a transaction by description, returning (category, confidence).

    Below the confidence threshold the transaction is marked uncategorized
    (Requirement 4.3).
    """
    text = (description or "").lower()
    for keywords, category, confidence in _CATEGORY_RULES:
        if any(kw in text for kw in keywords):
            if (
                confidence < CATEGORY_CONFIDENCE_THRESHOLD
            ):  # pragma: no cover - no rule below threshold
                return "uncategorized", confidence
            return category, confidence
    return "uncategorized", 0.0


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def parse_transaction(raw: dict[str, Any]) -> Transaction:
    """Parse a single transaction row."""
    desc = str(raw.get("description", ""))
    category, conf = categorize(desc)
    is_bounced = any(kw in desc.lower() for kw in _BOUNCE_KEYWORDS)
    return Transaction(
        txn_date=_to_date(raw.get("date") or raw.get("txn_date")),
        description=desc,
        debit=_num(raw.get("debit")),
        credit=_num(raw.get("credit")),
        balance=_num(raw.get("balance")),
        category=category,
        category_confidence=conf,
        counterparty=raw.get("counterparty"),
        is_bounced=is_bounced,
    )


def _conduct(transactions: list[Transaction]) -> ConductMetrics:
    """Compute conduct metrics from transactions (Requirement 4.4/4.5)."""
    if not transactions:
        return ConductMetrics()
    balances = [t.balance for t in transactions]
    # Monthly average balance: mean of end-of-day balances grouped by month.
    by_month: dict[str, list[float]] = {}
    for t in transactions:
        by_month.setdefault(t.txn_date.strftime("%Y-%m"), []).append(t.balance)
    monthly_avgs = [sum(v) / len(v) for v in by_month.values()]
    mab = sum(monthly_avgs) / len(monthly_avgs) if monthly_avgs else 0.0

    bounced = sum(1 for t in transactions if t.is_bounced)
    overdrafts = sum(1 for b in balances if b < 0)
    return ConductMetrics(
        monthly_average_balance=round(mab, 2),
        peak_balance=max(balances),
        minimum_balance=min(balances),
        total_credits=round(sum(t.credit for t in transactions), 2),
        total_debits=round(sum(t.debit for t in transactions), 2),
        bounced_transaction_count=bounced,
        overdraft_instances=overdrafts,
    )


def parse_bank_statement(raw: dict[str, Any]) -> BankStatement:
    """Parse a full bank statement payload into the schema with conduct metrics."""
    txns = [parse_transaction(t) for t in raw.get("transactions", [])]
    txns.sort(key=lambda t: t.txn_date)
    conduct = _conduct(txns)

    period_start = txns[0].txn_date if txns else None
    period_end = txns[-1].txn_date if txns else None

    stmt = BankStatement(
        account_number=str(raw.get("account_number", "")),
        bank_name=raw.get("bank_name"),
        period_start=period_start,
        period_end=period_end,
        transactions=txns,
        conduct=conduct,
    )

    uncategorized = sum(1 for t in txns if t.category == "uncategorized")
    if uncategorized:
        stmt.flags.append(
            Flag(
                code="UNCATEGORIZED_TRANSACTIONS",
                message=f"{uncategorized}/{len(txns)} transactions below {CATEGORY_CONFIDENCE_THRESHOLD:.0%} confidence.",
                severity=Severity.LOW,
            )
        )
    if conduct.bounced_transaction_count:
        stmt.flags.append(
            Flag(
                code="BOUNCED_TRANSACTIONS",
                message=f"{conduct.bounced_transaction_count} bounced transaction(s) detected.",
                severity=Severity.HIGH,
            )
        )
    if conduct.overdraft_instances:
        stmt.flags.append(
            Flag(
                code="OVERDRAFT_INSTANCES",
                message=f"{conduct.overdraft_instances} overdraft instance(s) detected.",
                severity=Severity.MEDIUM,
            )
        )
    logger.info("Parsed bank statement with %d transactions", len(txns))
    return stmt
