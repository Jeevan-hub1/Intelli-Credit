"""Financial ratio calculations used by the scoring module."""

from __future__ import annotations

from models.financial import FinancialStatement
from utils.numeric import safe_div


def dscr(stmt: FinancialStatement) -> float:
    """Debt Service Coverage Ratio (Requirement 10.2).

    DSCR = (Net Operating Income) / (Debt Service).
    Net operating income approximated as EBITDA; debt service as interest +
    scheduled principal repayment (from cash flow when available).
    """
    pl = stmt.profit_and_loss
    principal = stmt.cash_flow.debt_repayment if stmt.cash_flow else 0.0
    debt_service = pl.interest_expense + principal
    noi = pl.ebitda or (pl.ebit + pl.depreciation)
    return round(safe_div(noi, debt_service, default=0.0), 4)


def icr(stmt: FinancialStatement) -> float:
    """Interest Coverage Ratio (Requirement 10.3) = EBIT / Interest."""
    pl = stmt.profit_and_loss
    ebit = pl.ebit or (pl.ebitda - pl.depreciation)
    return round(safe_div(ebit, pl.interest_expense, default=0.0), 4)


def debt_to_equity(stmt: FinancialStatement) -> float:
    """Debt-to-Equity ratio (Requirement 11.2)."""
    bs = stmt.balance_sheet
    return round(safe_div(bs.total_debt, bs.equity, default=0.0), 4)


def current_ratio(stmt: FinancialStatement) -> float:
    """Current Ratio (Requirement 11.3)."""
    bs = stmt.balance_sheet
    return round(safe_div(bs.current_assets, bs.current_liabilities, default=0.0), 4)


def quick_ratio(stmt: FinancialStatement) -> float:
    """Quick Ratio (Requirement 11.3) excluding inventory."""
    bs = stmt.balance_sheet
    return round(safe_div(bs.current_assets - bs.inventory, bs.current_liabilities, default=0.0), 4)
