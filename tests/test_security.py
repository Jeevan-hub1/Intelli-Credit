"""Unit tests for security services (Requirement 19, 23)."""

import pytest

from models.base import UserRole
from services import security


def test_encrypt_decrypt_roundtrip():
    secret = "PAN:ABCDE1234F sensitive"
    token = security.encrypt_text(secret)
    assert token != secret
    assert security.decrypt_text(token) == secret


def test_encrypt_bytes_roundtrip():
    data = b"\x00\x01binary-data\xff"
    assert security.decrypt_bytes(security.encrypt_bytes(data)) == data


def test_password_hash_and_verify():
    h = security.hash_password("s3cret!")
    assert h.startswith("pbkdf2_sha256$")
    assert security.verify_password("s3cret!", h)
    assert not security.verify_password("wrong", h)
    assert not security.verify_password("x", "not-a-valid-hash")


def test_password_hash_is_salted():
    assert security.hash_password("same") != security.hash_password("same")


def test_jwt_create_and_decode():
    token = security.create_access_token(subject="u1", role=UserRole.CREDIT_OFFICER)
    payload = security.decode_access_token(token)
    assert payload["sub"] == "u1"
    assert payload["role"] == "Credit_Officer"


def test_rbac_role_hierarchy():
    assert security.role_allows(UserRole.ADMINISTRATOR, UserRole.ANALYST)
    assert security.role_allows(UserRole.CREDIT_OFFICER, UserRole.ANALYST)
    assert not security.role_allows(UserRole.ANALYST, UserRole.CREDIT_OFFICER)
    assert security.role_allows(UserRole.ANALYST, UserRole.ANALYST)


def test_require_role_raises():
    security.require_role(UserRole.ADMINISTRATOR, UserRole.CREDIT_OFFICER)  # ok
    with pytest.raises(security.AuthorizationError):
        security.require_role(UserRole.ANALYST, UserRole.ADMINISTRATOR)
