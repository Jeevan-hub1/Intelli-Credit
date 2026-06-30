"""Shared pytest fixtures and sample data builders."""
from __future__ import annotations

import pytest


@pytest.fixture
def sample_financials() -> list[dict]:
    """Three years of healthy, internally consistent financials."""
    return [
        {"fiscal_year": 2023,
         "balance_sheet": {"total_assets": 5000, "total_liabilities": 3000, "equity": 2000,
                           "total_debt": 2500, "current_assets": 2000, "current_liabilities": 1000,
                           "inventory": 500, "net_worth": 2000},
         "profit_and_loss": {"revenue": 10000, "ebitda": 1800, "ebit": 1500, "depreciation": 300,
                             "interest_expense": 600, "net_profit": 700},
         "cash_flow": {"operating_cash_flow": 1200, "debt_repayment": 400}},
        {"fiscal_year": 2022,
         "balance_sheet": {"total_assets": 4500, "total_liabilities": 2800, "equity": 1700,
                           "total_debt": 2300, "current_assets": 1800, "current_liabilities": 950,
                           "inventory": 450, "net_worth": 1700},
         "profit_and_loss": {"revenue": 8500, "ebitda": 1500, "ebit": 1250, "depreciation": 250,
                             "interest_expense": 550, "net_profit": 550}},
        {"fiscal_year": 2021,
         "balance_sheet": {"total_assets": 4000, "total_liabilities": 2500, "equity": 1500,
                           "total_debt": 2100, "current_assets": 1600, "current_liabilities": 900,
                           "inventory": 400, "net_worth": 1500},
         "profit_and_loss": {"revenue": 7000, "ebitda": 1200, "ebit": 1000, "depreciation": 200,
                             "interest_expense": 500, "net_profit": 400}},
    ]


@pytest.fixture
def cycle_edges() -> list[dict]:
    """A circular-trading cycle A->B->C->A within 90 days, each > 10 lakhs."""
    return [
        {"from": "A", "to": "B", "amount": 1_500_000, "date": "2024-01-01"},
        {"from": "B", "to": "C", "amount": 1_400_000, "date": "2024-01-20"},
        {"from": "C", "to": "A", "amount": 1_300_000, "date": "2024-02-10"},
    ]
