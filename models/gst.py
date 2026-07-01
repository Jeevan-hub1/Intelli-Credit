"""GST return domain models (Requirements 3, 6)."""

from __future__ import annotations

from typing import Optional

from pydantic import Field

from models.base import Flag, IntelliBaseModel


class GSTLineEntry(IntelliBaseModel):
    """A counterparty entry within a GST return."""

    gstin: str
    counterparty_name: Optional[str] = None
    taxable_value: float = 0.0
    igst: float = 0.0
    cgst: float = 0.0
    sgst: float = 0.0
    invoice_count: int = 0

    @property
    def total_tax(self) -> float:
        return self.igst + self.cgst + self.sgst


class GSTReturn(IntelliBaseModel):
    """A single-period GST return (GSTR-2A or GSTR-3B)."""

    form_type: str = Field(..., description="GSTR-2A | GSTR-3B")
    period: str = Field(..., description="MM-YYYY")
    gstin: str
    total_taxable_value: float = 0.0
    total_itc: float = Field(default=0.0, description="Input tax credit")
    total_tax_liability: float = 0.0
    entries: list[GSTLineEntry] = Field(default_factory=list)


class ITCReconciliation(IntelliBaseModel):
    """Reconciliation between GSTR-2A and GSTR-3B ITC (Requirement 3.2/3.3)."""

    period: str
    itc_2a: float = 0.0
    itc_3b: float = 0.0
    variance_amount: float = 0.0
    variance_pct: float = 0.0
    flagged: bool = False


class RevenueTrendPoint(IntelliBaseModel):
    """Month-over-month revenue point derived from GSTR-3B (Requirement 3.5)."""

    period: str
    revenue: float = 0.0
    mom_change_pct: Optional[float] = None


class GSTData(IntelliBaseModel):
    """Aggregated GST analysis for a borrower."""

    gstin: str
    returns: list[GSTReturn] = Field(default_factory=list)
    reconciliations: list[ITCReconciliation] = Field(default_factory=list)
    revenue_trend: list[RevenueTrendPoint] = Field(default_factory=list)
    supplier_gstins: list[str] = Field(default_factory=list)
    customer_gstins: list[str] = Field(default_factory=list)
    itc_to_revenue_ratio: float = 0.0
    flags: list[Flag] = Field(default_factory=list)
