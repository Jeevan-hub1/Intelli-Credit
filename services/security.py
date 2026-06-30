"""Security services: encryption, password hashing, JWT, and RBAC (Req 19, 23).

- AES-256 encryption of stored data via Fernet (Requirement 19.1).
- Password hashing with PBKDF2-HMAC-SHA256 (no external bcrypt dependency).
- OAuth2-style JWT bearer tokens (Requirement 23.2).
- Role-based access control across Analyst/Credit_Officer/Administrator.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

from config.settings import settings
from models.base import UserRole
from utils.logging import get_logger

logger = get_logger(__name__)

_PBKDF2_ROUNDS = 200_000
_JWT_ALGORITHM = "HS256"


# ----------------------------- Encryption ---------------------------------

def _derive_fernet_key() -> bytes:
    """Derive/obtain a 32-byte urlsafe-base64 key for Fernet (AES-256)."""
    configured = getattr(settings, "encryption_key", None)
    if configured:
        # Accept either a ready Fernet key or any secret to be hashed to 32 bytes.
        try:
            base64.urlsafe_b64decode(configured)
            if len(configured) == 44:
                return configured.encode()
        except Exception:
            pass
        digest = hashlib.sha256(configured.encode()).digest()
        return base64.urlsafe_b64encode(digest)
    # Dev fallback: derive deterministically from the app secret key.
    digest = hashlib.sha256(settings.secret_key.encode()).digest()
    return base64.urlsafe_b64encode(digest)



def _fernet():
    from cryptography.fernet import Fernet

    return Fernet(_derive_fernet_key())


def encrypt_bytes(data: bytes) -> bytes:
    """Encrypt bytes with AES-256 (Fernet) (Requirement 19.1)."""
    return _fernet().encrypt(data)


def decrypt_bytes(token: bytes) -> bytes:
    """Decrypt bytes previously encrypted with encrypt_bytes."""
    return _fernet().decrypt(token)


def encrypt_text(text: str) -> str:
    return encrypt_bytes(text.encode()).decode()


def decrypt_text(token: str) -> str:
    return decrypt_bytes(token.encode()).decode()


# --------------------------- Password hashing ------------------------------

def hash_password(password: str) -> str:
    """Hash a password with a random salt using PBKDF2-HMAC-SHA256."""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Verify a password against a stored PBKDF2 hash (constant-time compare)."""
    try:
        algo, rounds, salt_hex, hash_hex = stored.split("$")
        assert algo == "pbkdf2_sha256"
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AssertionError):
        return False



# ------------------------------- JWT tokens --------------------------------

def create_access_token(*, subject: str, role: UserRole,
                        expires_minutes: Optional[int] = None) -> str:
    """Create a signed JWT access token (Requirement 23.2)."""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.access_token_expire_minutes
    )
    payload = {"sub": subject, "role": role.value if isinstance(role, UserRole) else str(role),
               "exp": expire, "iat": datetime.now(timezone.utc)}
    return jwt.encode(payload, settings.secret_key, algorithm=_JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode/verify a JWT; raises jwt exceptions on invalid/expired tokens."""
    return jwt.decode(token, settings.secret_key, algorithms=[_JWT_ALGORITHM])


# --------------------------------- RBAC ------------------------------------

# Higher number = more privilege. Higher roles inherit lower-role permissions.
_ROLE_RANK = {UserRole.ANALYST: 1, UserRole.CREDIT_OFFICER: 2, UserRole.ADMINISTRATOR: 3}


def role_allows(user_role: UserRole, required: UserRole) -> bool:
    """Return True if user_role meets or exceeds the required role."""
    return _ROLE_RANK.get(user_role, 0) >= _ROLE_RANK.get(required, 99)


class AuthorizationError(Exception):
    """Raised when an authenticated user lacks the required role."""


def require_role(user_role: UserRole, required: UserRole) -> None:
    """Enforce a minimum role, raising AuthorizationError otherwise."""
    if not role_allows(user_role, required):
        raise AuthorizationError(
            f"Role '{getattr(user_role, 'value', user_role)}' lacks required "
            f"'{getattr(required, 'value', required)}' privilege."
        )
