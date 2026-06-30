"""Bank statement domain models (Requirements 4, 5)."""
from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import Field

from models.base import Flag, IntelliBaseModel


class Transaction(IntelliBaseModel):
    """A single bank transaction (Requirement 4.1)."""

    txn_date: date
    description: str
    debit: float = 0.0
    credit: float = 0.0
    balance: float = 0.0
    category: str = Field(default="uncategorized")
    category_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    counterparty: Optional[str] = Field(default=None, description="Resolved counterparty/entity")
    is_bounced: bool = False


class ConductMetrics(IntelliBaseModel):
    """Banking conduct metrics (Requirement 4.4/4.5)."""

    monthly_average_balance: float = 0.0
    peak_balance: float = 0.0
    minimum_balance: float = 0.0
    total_credits: float = 0.0
    total_debits: float = 0.0
    bounced_transaction_count: int = 0
    overdraft_instances: int = 0


class BankStatement(IntelliBaseModel):
    """A parsed bank statement with transactions and conduct metrics."""

    account_number: str
    bank_name: Optional[str] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    transactions: list[Transaction] = Field(default_factory=list)
    conduct: ConductMetrics = Field(default_factory=ConductMetrics)
    flags: list[Flag] = Field(default_factory=list)
