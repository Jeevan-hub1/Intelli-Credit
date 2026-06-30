"""Application and document domain models."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import Field

from models.base import (
    ApplicationStatus,
    DocumentFormat,
    DocumentType,
    Flag,
    IntelliBaseModel,
    utcnow,
)


def new_id(prefix: str) -> str:
    """Generate a prefixed unique identifier."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Borrower(IntelliBaseModel):
    """Borrower (corporate applicant) details."""

    name: str
    cin: Optional[str] = Field(default=None, description="Corporate Identification Number")
    gstin: Optional[str] = Field(default=None, description="Primary GSTIN")
    pan: Optional[str] = None
    industry: str = Field(default="unclassified")
    incorporation_date: Optional[date] = None
    business_description: Optional[str] = None
    director_names: list[str] = Field(default_factory=list)
    director_dins: list[str] = Field(default_factory=list)


class Document(IntelliBaseModel):
    """An uploaded document with classification metadata (Requirement 1)."""

    id: str = Field(default_factory=lambda: new_id("doc"))
    filename: str
    fmt: DocumentFormat
    size_bytes: int = 0
    storage_key: Optional[str] = None
    doc_type: DocumentType = DocumentType.UNKNOWN
    classification_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_manual_review: bool = False
    uploaded_at: datetime = Field(default_factory=utcnow)
    flags: list[Flag] = Field(default_factory=list)
    error_code: Optional[str] = None
    error_message: Optional[str] = None



class CollateralItem(IntelliBaseModel):
    """A pledged collateral asset (Requirement 12)."""

    description: str
    collateral_type: str = Field(..., description="property | inventory | receivables | equipment")
    market_value: float = Field(..., ge=0.0)


class LoanRequest(IntelliBaseModel):
    """Requested facility details."""

    amount: float = Field(..., gt=0.0, description="Requested loan amount in INR")
    tenure_months: int = Field(..., gt=0)
    purpose: Optional[str] = None
    collateral: list[CollateralItem] = Field(default_factory=list)


class Application(IntelliBaseModel):
    """A credit application aggregating borrower, loan, and documents."""

    id: str = Field(default_factory=lambda: new_id("app"))
    borrower: Borrower
    loan_request: LoanRequest
    status: ApplicationStatus = ApplicationStatus.CREATED
    documents: list[Document] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    assigned_officer_id: Optional[str] = None
    model_version: Optional[str] = None

    def add_document(self, document: Document) -> None:
        """Attach a document and advance status if appropriate."""
        self.documents.append(document)
        if self.status == ApplicationStatus.CREATED:
            self.status = ApplicationStatus.DOCUMENTS_UPLOADED
        self.updated_at = utcnow()
