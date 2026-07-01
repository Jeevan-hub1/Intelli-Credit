"""Document extraction layer (Requirement 1 - feeds the parsers).

Turns raw uploaded bytes into the structured records the parsing services
consume. Deterministic backends (JSON, CSV) are always available; richer
backends (PDF text, OCR, LLM structuring) activate only when their optional
dependencies are installed, so the pipeline degrades gracefully.

Pipeline:  raw bytes -> extract_text()/extract_structured() -> parser dict
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Optional

from models.base import DocumentType
from utils.logging import get_logger
from utils.numeric import safe_div  # noqa: F401  (kept for downstream use)

logger = get_logger(__name__)


class ExtractionError(Exception):
    """Raised when a document cannot be extracted into a usable form."""


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="ignore")


def extract_text(filename: str, data: bytes) -> str:
    """Extract plain text from a document.

    CSV/TXT/JSON decode directly; PDF and image OCR use optional libraries
    (pypdf / pytesseract) when available.
    """
    lower = filename.lower()
    if lower.endswith((".csv", ".txt", ".json")):
        return _decode(data)
    if lower.endswith(".pdf"):
        return _extract_pdf_text(data)
    if lower.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff")):
        return _extract_image_text(data)
    # Fallback: best-effort decode.
    return _decode(data)


def _extract_pdf_text(data: bytes) -> str:  # pragma: no cover - optional pypdf dependency
    try:
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:
        logger.info("PDF text extraction unavailable/failed: %s", exc)
        return _decode(data)


def _extract_image_text(data: bytes) -> str:  # pragma: no cover - optional OCR dependency
    try:
        import pytesseract
        from PIL import Image

        return pytesseract.image_to_string(Image.open(io.BytesIO(data)))
    except Exception as exc:
        logger.info("OCR unavailable/failed: %s", exc)
        return ""


def _num(value: Any) -> float:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _bank_from_csv(text: str, filename: str) -> dict:
    """Parse a bank-statement CSV into the bank_parser input shape.

    Expected headers (case-insensitive): date, description, debit, credit,
    balance, counterparty (counterparty optional).
    """
    reader = csv.DictReader(io.StringIO(text))
    txns: list[dict] = []
    for row in reader:
        norm = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        if not norm.get("date"):
            continue
        txns.append(
            {
                "date": norm.get("date"),
                "description": norm.get("description", ""),
                "debit": _num(norm.get("debit")),
                "credit": _num(norm.get("credit")),
                "balance": _num(norm.get("balance")),
                "counterparty": norm.get("counterparty") or None,
            }
        )
    return {"account_number": filename.rsplit(".", 1)[0], "transactions": txns}


def _maybe_json(text: str) -> Optional[Any]:
    """Return parsed JSON if the text is a JSON document, else None."""
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def extract_structured(doc_type: DocumentType, filename: str, data: bytes) -> dict:
    """Extract a structured record ready for the domain parsers.

    Returns a dict keyed by the target parser input, e.g.
    ``{"kind": "bank", "data": {...}}`` or ``{"kind": "financials", "data": [...]}``.
    JSON uploads are accepted as a direct passthrough for any document type.
    """
    text = extract_text(filename, data)

    payload = _maybe_json(text)
    if payload is not None:
        kind = _kind_for(doc_type)
        return {"kind": kind, "data": payload, "source": "json"}

    lower = filename.lower()
    if doc_type == DocumentType.BANK_STATEMENT and lower.endswith(".csv"):
        return {"kind": "bank", "data": _bank_from_csv(text, filename), "source": "csv"}

    # Text was extracted but no structured backend matched; return the raw text
    # so an LLM/structuring step (or manual review) can take over.
    return {"kind": "text", "data": {"text": text[:5000]}, "source": "text"}


def _kind_for(doc_type: DocumentType) -> str:
    mapping = {
        DocumentType.BANK_STATEMENT: "bank",
        DocumentType.GST_RETURN: "gst",
        DocumentType.FINANCIAL_STATEMENT: "financials",
        DocumentType.ANNUAL_REPORT: "financials",
    }
    return mapping.get(doc_type, "text")


def parse_extracted(extracted: dict):
    """Route an extracted record to the matching domain parser.

    Returns the parsed domain model, or None when the content is unstructured
    text (deferred to an LLM/manual step).
    """
    kind, data = extracted.get("kind"), extracted.get("data")
    if kind == "bank":
        from services.bank_parser import parse_bank_statement

        return parse_bank_statement(data)
    if kind == "gst":
        from services.gst_parser import parse_gst

        return parse_gst(data if isinstance(data, list) else [data])
    if kind == "financials":
        from services.financial_parser import parse_financials

        return parse_financials(data if isinstance(data, list) else [data])
    return None
