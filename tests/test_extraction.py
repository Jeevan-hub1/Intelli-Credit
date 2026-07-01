"""Tests for the document extraction layer."""

import json

from models.base import DocumentType
from services import extraction as ex


def test_extract_text_variants():
    assert "hello" in ex.extract_text("a.csv", b"hello,world")
    assert ex.extract_text("a.txt", b"plain text") == "plain text"
    # Unknown extension falls back to a best-effort decode.
    assert ex.extract_text("a.bin", b"raw bytes") == "raw bytes"


def test_extract_text_pdf_and_image_dispatch():
    # pypdf / pytesseract aren't installed here, so these degrade gracefully:
    # the PDF path falls back to a byte decode, and OCR returns an empty string.
    assert ex.extract_text("scan.pdf", b"pdf-ish bytes") is not None
    assert isinstance(ex.extract_text("scan.png", b"\x89PNG image bytes"), str)


def test_num_coercion():
    assert ex._num("1,234.5") == 1234.5
    assert ex._num("bad") == 0.0
    assert ex._num(None) == 0.0


def test_maybe_json():
    assert ex._maybe_json('{"a": 1}') == {"a": 1}
    assert ex._maybe_json("[1, 2]") == [1, 2]
    assert ex._maybe_json("not json") is None
    assert ex._maybe_json("{bad json}") is None
    assert ex._maybe_json("") is None


def test_extract_structured_bank_csv():
    csv = (
        b"date,description,debit,credit,balance,counterparty\n"
        b"2024-01-05,Salary,0,5000,50000,ACME\n"
        b",skip empty date,0,0,0,\n"
        b"2024-01-10,NEFT,2000,0,48000,VEND1\n"
    )
    out = ex.extract_structured(DocumentType.BANK_STATEMENT, "stmt.csv", csv)
    assert out["kind"] == "bank" and out["source"] == "csv"
    assert len(out["data"]["transactions"]) == 2  # empty-date row skipped
    parsed = ex.parse_extracted(out)
    assert len(parsed.transactions) == 2


def test_extract_structured_json_passthrough():
    payload = [
        {
            "fiscal_year": 2023,
            "balance_sheet": {"total_assets": 100, "total_liabilities": 60, "equity": 40},
            "profit_and_loss": {"revenue": 500},
        }
    ]
    out = ex.extract_structured(
        DocumentType.FINANCIAL_STATEMENT, "fin.json", json.dumps(payload).encode()
    )
    assert out["kind"] == "financials" and out["source"] == "json"
    parsed = ex.parse_extracted(out)
    assert len(parsed.statements) == 1


def test_extract_structured_gst_json_dict():
    payload = {
        "form_type": "GSTR-3B",
        "period": "2024-01",
        "gstin": "27A",
        "total_taxable_value": 1000,
        "total_itc": 100,
    }
    out = ex.extract_structured(DocumentType.GST_RETURN, "gst.json", json.dumps(payload).encode())
    assert out["kind"] == "gst"
    parsed = ex.parse_extracted(out)  # dict wrapped into a list
    assert parsed.gstin == "27A"


def test_extract_structured_text_fallback_and_parse_none():
    out = ex.extract_structured(DocumentType.LEGAL_NOTICE, "notice.txt", b"some legal text")
    assert out["kind"] == "text"
    assert ex.parse_extracted(out) is None


def test_kind_for_mapping():
    assert ex._kind_for(DocumentType.ANNUAL_REPORT) == "financials"
    assert ex._kind_for(DocumentType.RATING_REPORT) == "text"
