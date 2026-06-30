"""Tests for fraud detection and external checks (Requirements 5-8)."""
from services.external_apis import check_mca21_compliance, search_ecourts, validate_gstin
from services.fraud_detector import (
    build_transaction_graph,
    detect_circular_trading,
    detect_fake_itc,
)
from models.gst import GSTData, GSTLineEntry, GSTReturn


def test_circular_trading_high_and_critical(cycle_edges):
    edges = build_transaction_graph(None, self_entity="A", extra_edges=cycle_edges)
    findings, ratio = detect_circular_trading(edges, total_revenue=5_000_000)
    severities = {f.severity.value for f in findings}
    assert "High" in severities
    assert ratio > 0.15 and "Critical" in severities
    chains = [f.cycles[0].entity_chain for f in findings if f.cycles]
    assert ["A", "B", "C", "A"] in chains


def test_no_cycle_no_findings():
    edges = build_transaction_graph(None, self_entity="A", extra_edges=[
        {"from": "A", "to": "B", "amount": 100, "date": "2024-01-01"},
    ])
    findings, ratio = detect_circular_trading(edges, total_revenue=1000)
    assert findings == [] and ratio == 0.0


def test_validate_gstin():
    assert validate_gstin("27AAPFU0939F1ZV")
    assert not validate_gstin("BADGSTIN")
    assert not validate_gstin("99AAPFU0939F1ZV")  # invalid state code


def test_fake_itc_invalid_supplier():
    gst = GSTData(gstin="27A", supplier_gstins=["BADONE"],
                  returns=[GSTReturn(form_type="GSTR-2A", period="2024-01", gstin="27A",
                                     entries=[GSTLineEntry(gstin="BADONE", taxable_value=600000,
                                                           invoice_count=1)])])
    findings = detect_fake_itc(gst, gstin_validator=validate_gstin)
    kinds = {f.kind for f in findings}
    assert "fake_itc" in kinds


def test_mca21_and_ecourts():
    comp = check_mca21_compliance("U1", filings=[{"type": "AR", "due_date": "2020-01-01", "filed": False}],
                                  directors=[{"din": "1", "name": "X", "disqualified": True}])
    assert not comp.is_compliant and comp.disqualified_directors == ["X"]
    lit = search_ecourts("Co", cases=[{"case_id": "1", "category": "criminal",
                                       "title": "fraud case", "monetary_value": 100}])
    assert lit.high_risk_count == 1
