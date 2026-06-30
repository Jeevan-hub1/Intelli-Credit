"""Durability (DB read fallback) and input-validation API tests."""
import pytest
from fastapi.testclient import TestClient

from api import store
from main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def auth(client):
    r = client.post("/auth/token", data={"username": "admin", "password": "admin123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _analyzed_app(client, auth, sample_financials):
    body = {"borrower": {"name": "Durable Co", "cin": "U1", "gstin": "27AAPFU0939F1ZV",
                         "industry": "manufacturing"},
            "loan_request": {"amount": 3000, "tenure_months": 36}}
    app_id = client.post("/applications", json=body, headers=auth).json()["application_id"]
    client.post(f"/applications/{app_id}/analyze",
                json={"raw_financials": sample_financials}, headers=auth)
    return app_id


def test_reads_survive_registry_wipe(client, auth, sample_financials):
    app_id = _analyzed_app(client, auth, sample_financials)
    # Simulate a process restart / different worker: clear the in-memory registry.
    store.registry.results.clear()
    store.registry.cam_versions.clear()

    # Reads must still succeed by falling back to the database.
    res = client.get(f"/applications/{app_id}/results", headers=auth)
    assert res.status_code == 200
    assert 0 <= res.json()["overall_score"] <= 100

    versions = client.get(f"/applications/{app_id}/cam/versions", headers=auth)
    assert versions.status_code == 200 and len(versions.json()) >= 1

    cam = client.get(f"/applications/{app_id}/cam", headers=auth)
    assert cam.status_code == 200 and cam.json()["application_id"] == app_id


def test_invalid_gstin_rejected(client, auth):
    body = {"borrower": {"name": "Bad", "gstin": "NOTAGSTIN"},
            "loan_request": {"amount": 1000, "tenure_months": 12}}
    r = client.post("/applications", json=body, headers=auth)
    assert r.status_code == 400 and r.json()["code"] == "MALFORMED_REQUEST"


def test_invalid_pan_rejected(client, auth):
    body = {"borrower": {"name": "Bad", "pan": "abc"},
            "loan_request": {"amount": 1000, "tenure_months": 12}}
    r = client.post("/applications", json=body, headers=auth)
    assert r.status_code == 400


def test_valid_identifiers_accepted(client, auth):
    body = {"borrower": {"name": "Good", "gstin": "27AAPFU0939F1ZV", "pan": "ABCDE1234F"},
            "loan_request": {"amount": 1000, "tenure_months": 12}}
    r = client.post("/applications", json=body, headers=auth)
    assert r.status_code == 201


def test_reports_endpoint(client, auth):
    body = {"period": "2026-06", "capital_base": 50_000_000,
            "loans": [{"borrower_id": "b1", "borrower_name": "Acme", "industry": "mfg",
                       "exposure": 8_000_000, "days_past_due": 120, "overall_score": 55}]}
    r = client.post("/reports/portfolio", json=body, headers=auth)
    assert r.status_code == 200
    assert r.json()["npa_breakdown"]["Substandard"] == 8_000_000
    assert r.json()["excel_key"] and r.json()["pdf_key"]
