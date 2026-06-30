"""FastAPI dependencies: OAuth2 auth, RBAC enforcement, and rate limiting."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from config.database import get_db
from config.settings import settings
from models.base import UserRole
from models.db_models import DBUser
from services import security
from utils.logging import get_logger

logger = get_logger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token", auto_error=True)


@dataclass
class CurrentUser:
    id: str
    username: str
    role: UserRole


def get_current_user(token: str = Depends(oauth2_scheme),
                     db: Session = Depends(get_db)) -> CurrentUser:
    """Resolve and validate the bearer token into the current user."""
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = security.decode_access_token(token)
        user_id = payload.get("sub")
        role = UserRole(payload.get("role"))
    except Exception:
        raise credentials_exc
    user = db.get(DBUser, user_id)
    if not user or not user.is_active:
        raise credentials_exc
    return CurrentUser(id=user.id, username=user.username, role=role)



def require_role(required: UserRole):
    """Dependency factory enforcing a minimum role (Requirement 19.2/19.3)."""

    def _dep(user: CurrentUser = Depends(get_current_user),
             db: Session = Depends(get_db)) -> CurrentUser:
        if not security.role_allows(user.role, required):
            from services.audit import record_access_denied
            record_access_denied(db, user_id=user.id, action="access",
                                  reason=f"requires {required.value}")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail=f"Requires role {required.value} or higher")
        return user

    return _dep


# ----------------------------- Rate limiting -------------------------------

_WINDOW_SECONDS = 60
_request_log: dict[str, deque] = defaultdict(deque)


def rate_limiter(request: Request) -> None:
    """Sliding-window rate limit of N requests/minute per client (Req 23.4)."""
    limit = settings.rate_limit_per_minute
    client = request.headers.get("x-api-client") or (request.client.host if request.client else "anon")
    now = time.monotonic()
    bucket = _request_log[client]
    while bucket and now - bucket[0] > _WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= limit:
        retry = int(_WINDOW_SECONDS - (now - bucket[0])) + 1
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit of {limit}/min exceeded.",
            headers={"Retry-After": str(retry)},
        )
    bucket.append(now)
