"""Financial statement domain models (Requirements 2, 10, 11, 24)."""
from __future__ import annotations

from typing import Optional

from pydantic import Field

from models.base import AccountingStandard, Flag, IntelliBaseModel


class LineItem(IntelliBaseModel):
    """A single parsed financial line item with extraction confidence."""

    label: str
    value: float = 0.0
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    needs_verification: bool = False
    source: Optional[str] = Field(default=None, description="e.g. 'Balance Sheet FY2023, Page 5'")


class BalanceSheet(IntelliBaseModel):
    """Normalized balance sheet for a single fiscal year."""

    fiscal_year: int
    total_assets: float = 0.0
    current_assets: float = 0.0
    non_current_assets: float = 0.0
    inventory: float = 0.0
    receivables: float = 0.0
    cash_and_equivalents: float = 0.0
    total_liabilities: float = 0.0
    current_liabilities: float = 0.0
    non_current_liabilities: float = 0.0
    total_debt: float = 0.0
    equity: float = 0.0
    net_worth: float = 0.0

    def balances(self, tolerance: float = 0.01) -> bool:
        """Accounting identity check: Assets = Liabilities + Equity (Req 24.1)."""
        expected = self.total_liabilities + self.equity
        if self.total_assets == 0 and expected == 0:
            return True
        denom = max(abs(self.total_assets), 1.0)
        return abs(self.total_assets - expected) / denom <= tolerance



class ProfitAndLoss(IntelliBaseModel):
    """Normalized profit & loss statement for a single fiscal year."""

    fiscal_year: int
    revenue: float = 0.0
    other_income: float = 0.0
    cogs: float = 0.0
    operating_expenses: float = 0.0
    ebitda: float = 0.0
    depreciation: float = 0.0
    interest_expense: float = 0.0
    ebit: float = 0.0
    profit_before_tax: float = 0.0
    tax: float = 0.0
    net_profit: float = 0.0

    @property
    def ebitda_margin(self) -> float:
        if not self.revenue:
            return 0.0
        return self.ebitda / self.revenue


class CashFlow(IntelliBaseModel):
    """Normalized cash flow statement for a single fiscal year."""

    fiscal_year: int
    operating_cash_flow: float = 0.0
    investing_cash_flow: float = 0.0
    financing_cash_flow: float = 0.0
    net_cash_flow: float = 0.0
    debt_repayment: float = 0.0


class FinancialStatement(IntelliBaseModel):
    """A full-year financial statement bundle."""

    fiscal_year: int
    standard: AccountingStandard = AccountingStandard.UNKNOWN
    balance_sheet: BalanceSheet
    profit_and_loss: ProfitAndLoss
    cash_flow: Optional[CashFlow] = None
    line_items: list[LineItem] = Field(default_factory=list)
    flags: list[Flag] = Field(default_factory=list)
    parse_confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class FinancialAnalysis(IntelliBaseModel):
    """Multi-year financial dataset (most-recent-first ordering expected)."""

    statements: list[FinancialStatement] = Field(default_factory=list)

    def years_sorted_desc(self) -> list[FinancialStatement]:
        return sorted(self.statements, key=lambda s: s.fiscal_year, reverse=True)

    @property
    def latest(self) -> Optional[FinancialStatement]:
        ordered = self.years_sorted_desc()
        return ordered[0] if ordered else None
