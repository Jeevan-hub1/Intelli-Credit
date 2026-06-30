"""End-to-end API tests (Requirements 21-23, 26, 30)."""
import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def auth(client):
    r = client.post("/auth/token", data={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _create_and_analyze(client, auth, sample_financials):
    body = {"borrower": {"name": "Acme Ltd", "cin": "U1", "gstin": "27AAPFU0939F1ZV",
                         "industry": "manufacturing"},
            "loan_request": {"amount": 3000, "tenure_months": 36,
                             "collateral": [{"description": "P", "collateral_type": "property",
                                             "market_value": 6000}]}}
    app_id = client.post("/applications", json=body, headers=auth).json()["application_id"]
    client.post(f"/applications/{app_id}/analyze",
                json={"raw_financials": sample_financials,
                      "mca21_filings": [{"type": "AR", "due_date": "2025-01-01", "filed": True}]},
                headers=auth)
    return app_id


def test_healthz(client):
    assert client.get("/healthz").json()["status"] == "ok"


def test_requires_auth(client):
    assert client.get("/applications/x/results").status_code == 401


def test_malformed_returns_400(client, auth):
    r = client.post("/applications", json={"borrower": {}}, headers=auth)
    assert r.status_code == 400
    assert r.json()["code"] == "MALFORMED_REQUEST"


def test_full_workflow(client, auth, sample_financials):
    app_id = _create_and_analyze(client, auth, sample_financials)
    res = client.get(f"/applications/{app_id}/results", headers=auth)
    assert res.status_code == 200
    assert 0 <= res.json()["overall_score"] <= 100

    # qualitative note
    r = client.post(f"/applications/{app_id}/notes",
                    json={"text": "site visit", "dimension": "Capacity",
                          "sentiment": "Adverse", "severity": "High"}, headers=auth)
    assert r.status_code == 200 and r.json()["score_impact"] < 0

    # override -> new CAM version
    r = client.post(f"/applications/{app_id}/override",
                    json={"dimension": "Collateral", "new_score": 90, "reason": "more collateral"},
                    headers=auth)
    assert r.status_code == 200 and r.json()["cam_version"] == 2

    versions = client.get(f"/applications/{app_id}/cam/versions", headers=auth).json()
    assert len(versions) == 2
    cmp = client.get(f"/applications/{app_id}/cam/compare", params={"v1": 1, "v2": 2}, headers=auth)
    assert cmp.status_code == 200

    fin = client.post(f"/applications/{app_id}/finalize", json={"approve": True}, headers=auth)
    assert fin.json()["is_final"]


def test_openapi_available(client):
    spec = client.get("/openapi.json").json()
    assert spec["openapi"].startswith("3.")
    assert "/applications" in spec["paths"]
