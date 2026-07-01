"""Coverage for stateless rehydration (override/notes after a registry wipe)."""

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
    tok = client.post("/auth/token", data={"username": "admin", "password": "admin123"}).json()
    return {"Authorization": f"Bearer {tok['access_token']}"}


def _analyzed(client, auth, sample_financials):
    app_id = client.post(
        "/applications",
        json={
            "borrower": {"name": "Rehydrate Co", "gstin": "27AAPFU0939F1ZV", "industry": "mfg"},
            "loan_request": {"amount": 3000, "tenure_months": 36},
        },
        headers=auth,
    ).json()["application_id"]
    client.post(
        f"/applications/{app_id}/analyze", json={"raw_financials": sample_financials}, headers=auth
    )
    return app_id


def test_override_after_registry_wipe_rehydrates(client, auth, sample_financials):
    app_id = _analyzed(client, auth, sample_financials)
    # Add a note first (persisted), so a later rehydrate must replay it.
    client.post(
        f"/applications/{app_id}/notes",
        json={
            "text": "early note",
            "dimension": "Capacity",
            "sentiment": "Adverse",
            "severity": "Medium",
        },
        headers=auth,
    )
    store.registry.results.clear()
    store.registry.cam_versions.clear()
    # Override triggers rehydrate from the snapshot + replay of the persisted note.
    r = client.post(
        f"/applications/{app_id}/override",
        json={"dimension": "Collateral", "new_score": 88, "reason": "more security"},
        headers=auth,
    )
    assert r.status_code == 200
    assert r.json()["cam_version"] == 2

    # Wipe again and add another note (rehydrate must replay note + prior override).
    store.registry.results.clear()
    store.registry.cam_versions.clear()
    r2 = client.post(
        f"/applications/{app_id}/notes",
        json={
            "text": "site visit",
            "dimension": "Capacity",
            "sentiment": "Adverse",
            "severity": "High",
        },
        headers=auth,
    )
    assert r2.status_code == 200

    # The rehydrated + replayed score should reflect the Collateral override (88).
    results = client.get(f"/applications/{app_id}/results", headers=auth).json()
    collateral = next(d for d in results["dimensions"] if d["dimension"] == "Collateral")
    assert collateral["score"] == 88 and collateral["overridden"] is True


def test_review_before_analysis_returns_404(client, auth):
    app_id = client.post(
        "/applications",
        json={"borrower": {"name": "Empty"}, "loan_request": {"amount": 1, "tenure_months": 1}},
        headers=auth,
    ).json()["application_id"]
    assert (
        client.post(f"/applications/{app_id}/notes", json={"text": "x"}, headers=auth).status_code
        == 404
    )
    assert (
        client.post(
            f"/applications/{app_id}/override",
            json={"dimension": "Capital", "new_score": 50, "reason": "because"},
            headers=auth,
        ).status_code
        == 404
    )
