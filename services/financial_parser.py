"""Financial statement parsing and normalization (Requirements 2, 24).

Accepts raw extracted financials (produced by an upstream OCR/LLM extraction
layer) as structured dicts, normalizes them into the standard schema, applies
per-line-item confidence flags, detects the reporting standard, and validates
the accounting identity Assets = Liabilities + Equity before output.
"""

from __future__ import annotations

from typing import Any

from models.base import AccountingStandard, Flag, Severity
from models.financial import (
    BalanceSheet,
    CashFlow,
    FinancialAnalysis,
    FinancialStatement,
    LineItem,
    ProfitAndLoss,
)
from utils.logging import get_logger

logger = get_logger(__name__)

LINE_ITEM_CONFIDENCE_THRESHOLD = 0.90  # Requirement 2.4
BALANCE_TOLERANCE = 0.01  # Requirement 2.6 / 24.1


def detect_standard(raw: dict[str, Any]) -> AccountingStandard:
    """Detect Indian GAAP vs Ind AS from hints in the raw payload."""
    declared = str(raw.get("standard", "")).lower()
    if "ind_as" in declared or "ind as" in declared or "indas" in declared:
        return AccountingStandard.IND_AS
    if "gaap" in declared or "schedule iii" in declared:
        return AccountingStandard.INDIAN_GAAP
    # Ind AS commonly reports "Other Comprehensive Income".
    text_blob = " ".join(str(v).lower() for v in raw.get("notes", []) if isinstance(v, str))
    if "other comprehensive income" in text_blob or "oci" in text_blob:
        return AccountingStandard.IND_AS
    return AccountingStandard.UNKNOWN


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _line_items(raw: dict[str, Any], source: str) -> list[LineItem]:
    """Build LineItem objects, flagging those below the confidence threshold."""
    items: list[LineItem] = []
    confidences: dict[str, float] = raw.get("confidences", {}) or {}
    for label, value in raw.items():
        if label in ("confidences", "standard", "notes", "fiscal_year", "type"):
            continue
        if not isinstance(value, (int, float)):
            continue
        conf = float(confidences.get(label, 1.0))
        items.append(
            LineItem(
                label=label,
                value=float(value),
                confidence=conf,
                needs_verification=conf < LINE_ITEM_CONFIDENCE_THRESHOLD,
                source=source,
            )
        )
    return items


def parse_year(raw: dict[str, Any]) -> FinancialStatement:
    """Normalize a single fiscal year's raw financials into the schema."""
    fy = int(raw.get("fiscal_year", 0))
    source = f"Financial Statement FY{fy}"
    bs_raw = raw.get("balance_sheet", {})
    pl_raw = raw.get("profit_and_loss", {})
    cf_raw = raw.get("cash_flow")

    balance_sheet = BalanceSheet(
        fiscal_year=fy,
        total_assets=_num(bs_raw.get("total_assets")),
        current_assets=_num(bs_raw.get("current_assets")),
        non_current_assets=_num(bs_raw.get("non_current_assets")),
        inventory=_num(bs_raw.get("inventory")),
        receivables=_num(bs_raw.get("receivables")),
        cash_and_equivalents=_num(bs_raw.get("cash_and_equivalents")),
        total_liabilities=_num(bs_raw.get("total_liabilities")),
        current_liabilities=_num(bs_raw.get("current_liabilities")),
        non_current_liabilities=_num(bs_raw.get("non_current_liabilities")),
        total_debt=_num(bs_raw.get("total_debt")),
        equity=_num(bs_raw.get("equity")),
        net_worth=_num(bs_raw.get("net_worth")) or _num(bs_raw.get("equity")),
    )
    pl = ProfitAndLoss(
        fiscal_year=fy,
        revenue=_num(pl_raw.get("revenue")),
        other_income=_num(pl_raw.get("other_income")),
        cogs=_num(pl_raw.get("cogs")),
        operating_expenses=_num(pl_raw.get("operating_expenses")),
        ebitda=_num(pl_raw.get("ebitda")),
        depreciation=_num(pl_raw.get("depreciation")),
        interest_expense=_num(pl_raw.get("interest_expense")),
        ebit=_num(pl_raw.get("ebit")),
        profit_before_tax=_num(pl_raw.get("profit_before_tax")),
        tax=_num(pl_raw.get("tax")),
        net_profit=_num(pl_raw.get("net_profit")),
    )
    cash_flow = None
    if cf_raw:
        cash_flow = CashFlow(
            fiscal_year=fy,
            operating_cash_flow=_num(cf_raw.get("operating_cash_flow")),
            investing_cash_flow=_num(cf_raw.get("investing_cash_flow")),
            financing_cash_flow=_num(cf_raw.get("financing_cash_flow")),
            net_cash_flow=_num(cf_raw.get("net_cash_flow")),
            debt_repayment=_num(cf_raw.get("debt_repayment")),
        )

    stmt = FinancialStatement(
        fiscal_year=fy,
        standard=detect_standard(raw),
        balance_sheet=balance_sheet,
        profit_and_loss=pl,
        cash_flow=cash_flow,
        line_items=_line_items(bs_raw, source) + _line_items(pl_raw, source),
    )
    _validate(stmt)
    return stmt


def _validate(stmt: FinancialStatement) -> None:
    """Validate accounting identities and logical consistency (Req 2.6, 24)."""
    bs = stmt.balance_sheet
    pl = stmt.profit_and_loss

    # Requirement 2.6 / 24.1: Assets = Liabilities + Equity.
    if not bs.balances(BALANCE_TOLERANCE):
        diff = bs.total_assets - (bs.total_liabilities + bs.equity)
        stmt.flags.append(
            Flag(
                code="BALANCE_SHEET_IMBALANCE",
                message=(
                    f"FY{stmt.fiscal_year} Balance Sheet does not balance: "
                    f"Assets {bs.total_assets:,.0f} vs Liabilities+Equity "
                    f"{bs.total_liabilities + bs.equity:,.0f} (diff {diff:,.0f})."
                ),
                severity=Severity.HIGH,
                context={"difference": diff},
            )
        )

    # Requirement 24.2: logical inconsistency checks.
    if pl.revenue < 0:
        stmt.flags.append(
            Flag(code="NEGATIVE_REVENUE", message="Revenue is negative.", severity=Severity.HIGH)
        )

    # Aggregate per-line-item confidence into a statement-level confidence.
    if stmt.line_items:
        stmt.parse_confidence = round(
            sum(li.confidence for li in stmt.line_items) / len(stmt.line_items), 4
        )
        low_conf = [li.label for li in stmt.line_items if li.needs_verification]
        if low_conf:
            stmt.flags.append(
                Flag(
                    code="LOW_LINE_ITEM_CONFIDENCE",
                    message=f"{len(low_conf)} line item(s) below {LINE_ITEM_CONFIDENCE_THRESHOLD:.0%} confidence.",
                    severity=Severity.MEDIUM,
                    context={"line_items": low_conf},
                )
            )


def parse_financials(raw_years: list[dict[str, Any]]) -> FinancialAnalysis:
    """Parse and normalize a multi-year set of raw financials."""
    statements = [parse_year(y) for y in raw_years if y]
    logger.info("Parsed %d financial statement year(s)", len(statements))
    return FinancialAnalysis(statements=statements)
