"""Coverage for RBAC denials and upload guards."""

import pytest
from fastapi.testclient import TestClient

from config.database import session_scope
from main import app
from models.base import UserRole
from models.db_models import DBUser
from services import security


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def admin(client):
    return client.post("/auth/token", data={"username": "admin", "password": "admin123"}).json()[
        "access_token"
    ]


@pytest.fixture(scope="module")
def analyst_token(client):
    # Create an Analyst-role user directly and mint a token.
    with session_scope() as db:
        uid = "analyst-user-1"
        if not db.get(DBUser, uid):
            db.add(
                DBUser(
                    id=uid,
                    username="analyst1",
                    role=UserRole.ANALYST.value,
                    password_hash=security.hash_password("x"),
                )
            )
    return security.create_access_token(subject="analyst-user-1", role=UserRole.ANALYST)


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_analyst_denied_credit_officer_endpoint(client, analyst_token):
    # /ews/check requires Credit_Officer; an Analyst must be forbidden.
    r = client.post("/ews/check", json={"borrower_id": "b1"}, headers=_h(analyst_token))
    assert r.status_code == 403


def test_upload_rejects_bad_content_type(client, admin):
    app_id = client.post(
        "/applications",
        json={"borrower": {"name": "C"}, "loan_request": {"amount": 1, "tenure_months": 1}},
        headers=_h(admin),
    ).json()["application_id"]
    files = {"file": ("x.exe", b"data", "application/x-msdownload")}
    r = client.post(f"/applications/{app_id}/documents", files=files, headers=_h(admin))
    assert r.status_code == 415


def test_upload_rejects_oversize(client, admin, monkeypatch):
    from services import document_parser

    monkeypatch.setattr(document_parser, "MAX_SYNC_SIZE_BYTES", 5)
    app_id = client.post(
        "/applications",
        json={"borrower": {"name": "C"}, "loan_request": {"amount": 1, "tenure_months": 1}},
        headers=_h(admin),
    ).json()["application_id"]
    files = {"file": ("big.csv", b"0123456789", "text/csv")}
    r = client.post(f"/applications/{app_id}/documents", files=files, headers=_h(admin))
    assert r.status_code == 413


def test_upload_to_missing_application(client, admin):
    files = {"file": ("x.csv", b"data", "text/csv")}
    r = client.post("/applications/nope/documents", files=files, headers=_h(admin))
    assert r.status_code == 404


def test_upload_valid_document_succeeds(client, admin):
    app_id = client.post(
        "/applications",
        json={"borrower": {"name": "C"}, "loan_request": {"amount": 1, "tenure_months": 1}},
        headers=_h(admin),
    ).json()["application_id"]
    files = {
        "file": ("fy.csv", b"Balance Sheet Profit and Loss Cash Flow Schedule III", "text/csv")
    }
    r = client.post(f"/applications/{app_id}/documents", files=files, headers=_h(admin))
    assert r.status_code == 200
    assert r.json()["doc_type"] == "financial_statement"
