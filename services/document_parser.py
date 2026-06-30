"""Document ingestion and classification service (Requirement 1).

Real OCR/PDF text extraction is pluggable; this service focuses on the
required behaviours: format validation, type classification with a
confidence threshold, manual-review flagging, and structured error codes.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from models.application import Document
from models.base import DocumentFormat, DocumentType, Flag, Severity
from utils.logging import get_logger

logger = get_logger(__name__)

CLASSIFICATION_THRESHOLD = 0.85          # Requirement 1.3
MAX_SYNC_SIZE_BYTES = 10 * 1024 * 1024   # Requirement 1.4 (<10MB)

# Format detection by extension and magic bytes.
_EXTENSION_FORMATS: dict[str, DocumentFormat] = {
    "pdf": DocumentFormat.PDF,
    "xlsx": DocumentFormat.EXCEL,
    "xls": DocumentFormat.EXCEL,
    "csv": DocumentFormat.CSV,
    "png": DocumentFormat.IMAGE,
    "jpg": DocumentFormat.IMAGE,
    "jpeg": DocumentFormat.IMAGE,
    "tif": DocumentFormat.IMAGE,
    "tiff": DocumentFormat.IMAGE,
}

# Keyword signals used to classify a document into a known type.
_TYPE_KEYWORDS: dict[DocumentType, tuple[str, ...]] = {
    DocumentType.ANNUAL_REPORT: ("annual report", "directors report", "board's report"),
    DocumentType.FINANCIAL_STATEMENT: ("balance sheet", "profit and loss", "cash flow", "schedule iii"),
    DocumentType.GST_RETURN: ("gstr", "gstin", "input tax credit", "gst return"),
    DocumentType.BANK_STATEMENT: ("statement of account", "available balance", "ifsc", "debit", "credit"),
    DocumentType.LEGAL_NOTICE: ("notice", "petitioner", "respondent", "tribunal", "court"),
    DocumentType.RATING_REPORT: ("rating", "crisil", "icra", "care ratings", "outlook"),
}


class DocumentError(Exception):
    """Raised on unrecoverable document ingestion errors."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message



@dataclass
class ClassificationResult:
    doc_type: DocumentType
    confidence: float


def detect_format(filename: str, data: Optional[bytes] = None) -> DocumentFormat:
    """Detect the document format from extension and/or magic bytes."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in _EXTENSION_FORMATS:
        return _EXTENSION_FORMATS[ext]
    if data:
        if data[:5] == b"%PDF-":
            return DocumentFormat.PDF
        if data[:2] == b"PK":  # zip-based (xlsx)
            return DocumentFormat.EXCEL
        if data[:3] == b"\xff\xd8\xff" or data[:8].startswith(b"\x89PNG"):
            return DocumentFormat.IMAGE
    raise DocumentError("ERR_UNSUPPORTED_FORMAT", f"Unsupported document format for '{filename}'")


def classify(text: str, *, hint: Optional[DocumentType] = None) -> ClassificationResult:
    """Classify document text into a known type with a confidence score.

    Confidence is derived from keyword-match density. A caller-provided hint
    (e.g. an explicit type during upload) yields high confidence.
    """
    if hint is not None:
        return ClassificationResult(hint, 0.99)

    lowered = (text or "").lower()
    scores: dict[DocumentType, int] = {}
    for dtype, keywords in _TYPE_KEYWORDS.items():
        scores[dtype] = sum(1 for kw in keywords if kw in lowered)

    best_type = max(scores, key=lambda k: scores[k])
    best_hits = scores[best_type]
    if best_hits == 0:
        return ClassificationResult(DocumentType.UNKNOWN, 0.0)

    total_keywords = len(_TYPE_KEYWORDS[best_type])
    # Confidence blends match ratio with separation from the runner-up.
    runner_up = sorted(scores.values(), reverse=True)[1] if len(scores) > 1 else 0
    ratio = best_hits / total_keywords
    separation = (best_hits - runner_up) / max(best_hits, 1)
    confidence = round(min(0.99, 0.5 * ratio + 0.5 * (0.5 + 0.5 * separation) + 0.15 * (best_hits >= 2)), 4)
    return ClassificationResult(best_type, confidence)



def ingest_document(
    filename: str,
    data: bytes,
    *,
    extracted_text: str = "",
    type_hint: Optional[DocumentType] = None,
    storage_key: Optional[str] = None,
) -> Document:
    """Ingest a document: validate, classify, and flag for review if needed.

    Returns a Document model. On hard failures raises DocumentError carrying a
    specific error code/description (Requirement 1.5).
    """
    started = time.monotonic()
    fmt = detect_format(filename, data)

    size = len(data)
    result = classify(extracted_text, hint=type_hint)

    doc = Document(
        filename=filename,
        fmt=fmt,
        size_bytes=size,
        storage_key=storage_key,
        doc_type=result.doc_type,
        classification_confidence=result.confidence,
    )

    # Requirement 1.3: flag low-confidence classifications for manual review.
    if result.confidence < CLASSIFICATION_THRESHOLD:
        doc.needs_manual_review = True
        doc.flags.append(
            Flag(
                code="LOW_CLASSIFICATION_CONFIDENCE",
                message=(
                    f"Classification confidence {result.confidence:.0%} is below "
                    f"{CLASSIFICATION_THRESHOLD:.0%}; manual review required."
                ),
                severity=Severity.MEDIUM,
                context={"doc_type": result.doc_type.value},
            )
        )

    # Requirement 1.4: surface a flag if a small file took too long.
    elapsed = time.monotonic() - started
    if size <= MAX_SYNC_SIZE_BYTES and elapsed > 30:
        doc.flags.append(
            Flag(
                code="SLOW_PROCESSING",
                message=f"Processing took {elapsed:.1f}s for a {size} byte file.",
                severity=Severity.LOW,
            )
        )

    logger.info(
        "Ingested document %s type=%s confidence=%.2f review=%s",
        filename, result.doc_type.value, result.confidence, doc.needs_manual_review,
    )
    return doc
