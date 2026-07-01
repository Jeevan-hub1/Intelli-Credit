"""Tests for parsing services (Requirements 1-4, 24)."""

from services.bank_parser import categorize, parse_bank_statement
from services.document_parser import ingest_document
from services.financial_parser import parse_financials
from services.gst_parser import parse_gst


def test_document_classification_confidence(sample_financials):
    doc = ingest_document(
        "fy23.csv", b"dummy", extracted_text="balance sheet profit and loss cash flow schedule iii"
    )
    assert doc.doc_type.value == "financial_statement"
    assert doc.classification_confidence >= 0.85
    assert not doc.needs_manual_review


def test_low_confidence_flags_manual_review():
    doc = ingest_document("misc.csv", b"dummy", extracted_text="hello world")
    assert doc.needs_manual_review
    assert any(f.code == "LOW_CLASSIFICATION_CONFIDENCE" for f in doc.flags)


def test_balance_sheet_imbalance_flagged():
    fa = parse_financials(
        [
            {
                "fiscal_year": 2023,
                "balance_sheet": {"total_assets": 1000, "total_liabilities": 600, "equity": 300},
                "profit_and_loss": {"revenue": 5000},
            }
        ]
    )
    assert not fa.latest.balance_sheet.balances()
    assert any(f.code == "BALANCE_SHEET_IMBALANCE" for f in fa.latest.flags)


def test_balanced_sheet_ok(sample_financials):
    fa = parse_financials(sample_financials)
    assert fa.latest.balance_sheet.balances()


def test_gst_itc_reconciliation_flag():
    gd = parse_gst(
        [
            {
                "form_type": "GSTR-2A",
                "period": "2024-01",
                "gstin": "27A",
                "total_itc": 100,
                "entries": [{"gstin": "27SUP1", "taxable_value": 500}],
            },
            {
                "form_type": "GSTR-3B",
                "period": "2024-01",
                "gstin": "27A",
                "total_itc": 120,
                "total_taxable_value": 1000,
            },
        ]
    )
    rec = gd.reconciliations[0]
    assert rec.variance_pct == 20.0 and rec.flagged
    assert "27SUP1" in gd.supplier_gstins


def test_bank_categorize_and_conduct():
    assert categorize("Salary credit")[0] == "salary"
    assert categorize("random text")[0] == "uncategorized"
    bs = parse_bank_statement(
        {
            "account_number": "1",
            "transactions": [
                {"date": "2024-01-05", "description": "Salary", "credit": 50000, "balance": 150000},
                {
                    "date": "2024-01-15",
                    "description": "ECS Return insufficient",
                    "debit": 0,
                    "balance": -5000,
                },
            ],
        }
    )
    assert bs.conduct.bounced_transaction_count == 1
    assert bs.conduct.overdraft_instances == 1
