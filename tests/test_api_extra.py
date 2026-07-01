"""Coverage for new API endpoints: auth lifecycle, jobs, list, idempotency, RBAC."""

import time

import pytest
from fastapi.testclient import TestClient

from main import app
from models.base import UserRole
from services import security


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def auth(client):
    r = client.post("/auth/token", data={"username": "admin", "password": "admin123"})
    return r.json()


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_login_returns_refresh_token(auth):
    assert auth["refresh_token"]
    assert auth["role"] == "Administrator"


def test_bad_login(client):
    r = client.post("/auth/token", data={"username": "admin", "password": "wrong"})
    assert r.status_code == 401


def test_refresh_flow(client, auth):
    r = client.post("/auth/refresh", json={"refresh_token": auth["refresh_token"]})
    assert r.status_code == 200 and r.json()["access_token"]
    bad = client.post("/auth/refresh", json={"refresh_token": "not-a-token"})
    assert bad.status_code == 401


def test_logout_revokes_token(client):
    tok = client.post("/auth/token", data={"username": "admin", "password": "admin123"}).json()
    access = tok["access_token"]
    assert client.post("/auth/logout", headers=_h(access)).status_code == 200
    # Revoked token can no longer be used.
    assert client.get("/applications", headers=_h(access)).status_code == 401


def test_invalid_token_rejected(client):
    assert client.get("/applications", headers=_h("garbage.token.here")).status_code == 401


def test_readyz(client):
    assert client.get("/readyz").json()["status"] == "ready"


def test_v1_alias(client):
    assert client.get("/v1/healthz").json()["status"] == "ok"


def _make_app(client, auth, sample_financials, idem=None):
    headers = _h(auth["access_token"])
    if idem:
        headers["Idempotency-Key"] = idem
    body = {
        "borrower": {"name": "Co", "gstin": "27AAPFU0939F1ZV", "industry": "manufacturing"},
        "loan_request": {"amount": 3000, "tenure_months": 36},
    }
    return client.post("/applications", json=body, headers=headers)


def test_list_applications_pagination(client, auth, sample_financials):
    _make_app(client, auth, sample_financials)
    r = client.get(
        "/applications",
        params={"limit": 5, "offset": 0, "sort": "created_at_asc"},
        headers=_h(auth["access_token"]),
    )
    assert r.status_code == 200
    body = r.json()
    assert "total" in body and "items" in body and body["limit"] == 5
    # Filter by industry.
    r2 = client.get(
        "/applications", params={"industry": "manufacturing"}, headers=_h(auth["access_token"])
    )
    assert r2.status_code == 200


def test_idempotency_key_replay(client, auth, sample_financials):
    key = "idem-key-123"
    r1 = _make_app(client, auth, sample_financials, idem=key)
    r2 = _make_app(client, auth, sample_financials, idem=key)
    assert r1.json()["application_id"] == r2.json()["application_id"]
    assert r2.json()["idempotent_replay"] is True


def test_async_analysis_job(client, auth, sample_financials):
    app_id = _make_app(client, auth, sample_financials).json()["application_id"]
    r = client.post(
        f"/applications/{app_id}/analyze",
        params={"async_mode": "true"},
        json={"raw_financials": sample_financials},
        headers=_h(auth["access_token"]),
    )
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    state = None
    for _ in range(60):
        state = client.get(f"/jobs/{job_id}", headers=_h(auth["access_token"])).json()
        if state["state"] in ("completed", "failed"):
            break
        time.sleep(0.1)
    assert state["state"] == "completed"
    assert state["result"]["overall_score"] >= 0
    assert client.get("/jobs/nonexistent", headers=_h(auth["access_token"])).status_code == 404


def test_analyze_missing_application(client, auth):
    r = client.post(
        "/applications/nope/analyze", json={"raw_financials": []}, headers=_h(auth["access_token"])
    )
    assert r.status_code == 404


def test_ews_check_and_digest(client, auth):
    r = client.post(
        "/ews/check",
        json={
            "borrower_id": "bx",
            "exposure_amount": 1_000_000,
            "days_financials_overdue": 70,
            "adverse_news_count": 3,
            "rating_downgraded": True,
        },
        headers=_h(auth["access_token"]),
    )
    assert r.status_code == 200 and r.json()["ews_score"] > 0
    d = client.get("/ews/digest", params={"top_n": 5}, headers=_h(auth["access_token"]))
    assert d.status_code == 200 and isinstance(d.json(), list)


def test_reports_portfolio(client, auth):
    r = client.post(
        "/reports/portfolio",
        json={
            "period": "2026-06",
            "capital_base": 50_000_000,
            "loans": [
                {
                    "borrower_id": "b1",
                    "borrower_name": "Acme",
                    "industry": "mfg",
                    "exposure": 8_000_000,
                    "days_past_due": 400,
                    "overall_score": 55,
                }
            ],
        },
        headers=_h(auth["access_token"]),
    )
    assert r.status_code == 200 and r.json()["excel_key"]


def test_cam_version_and_compare_errors(client, auth, sample_financials):
    app_id = client.post(
        "/applications",
        json={"borrower": {"name": "CamCo"}, "loan_request": {"amount": 1000, "tenure_months": 12}},
        headers=_h(auth["access_token"]),
    ).json()["application_id"]
    client.post(
        f"/applications/{app_id}/analyze",
        json={"raw_financials": sample_financials},
        headers=_h(auth["access_token"]),
    )
    # Unknown CAM version.
    assert (
        client.get(
            f"/applications/{app_id}/cam", params={"version": 99}, headers=_h(auth["access_token"])
        ).status_code
        == 404
    )
    # Compare with a missing version.
    assert (
        client.get(
            f"/applications/{app_id}/cam/compare",
            params={"v1": 1, "v2": 99},
            headers=_h(auth["access_token"]),
        ).status_code
        == 404
    )


def test_missing_resources_404(client, auth):
    h = _h(auth["access_token"])
    assert client.get("/applications/nope/results", headers=h).status_code == 404
    assert client.get("/applications/nope/cam", headers=h).status_code == 404
    assert (
        client.post("/applications/nope/finalize", json={"approve": True}, headers=h).status_code
        == 404
    )


def test_refresh_with_unknown_user(client):
    ghost = security.create_refresh_token(subject="ghost-user", role=UserRole.ANALYST)
    r = client.post("/auth/refresh", json={"refresh_token": ghost})
    assert r.status_code == 401


def test_token_for_unknown_user_rejected(client):
    ghost = security.create_access_token(subject="ghost-user-2", role=UserRole.ANALYST)
    assert client.get("/applications", headers=_h(ghost)).status_code == 401


def test_list_applications_status_filter(client, auth):
    r = client.get(
        "/applications",
        params={"status_filter": "created", "limit": 500},
        headers=_h(auth["access_token"]),
    )
    assert r.status_code == 200


def test_store_helpers_edge_cases():
    from api import store
    from config.database import session_scope

    # rehydrate returns None for an unknown application.
    with session_scope() as db:
        assert store.rehydrate(db, "does-not-exist") is None
        # persist_analysis_input for an unknown app is a safe no-op.
        store.persist_analysis_input(db, "does-not-exist", {}, model_version="1", generated_by="u")
