"""Intelli-Credit (CreditDNA) FastAPI application entrypoint."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from api.routes import router
from config.database import init_db, session_scope
from config.settings import settings
from models.base import UserRole
from models.db_models import DBUser
from services import security
from utils.logging import get_logger

logger = get_logger(__name__)


def _seed_admin() -> None:
    """Create a default administrator account if none exists (dev convenience)."""
    with session_scope() as db:
        if db.query(DBUser).count() > 0:
            return
        username = os.getenv("ADMIN_USERNAME", "admin")
        password = os.getenv("ADMIN_PASSWORD", "admin123")
        db.add(
            DBUser(
                username=username,
                email="admin@intelli-credit.local",
                role=UserRole.ADMINISTRATOR.value,
                password_hash=security.hash_password(password),
            )
        )
        logger.info("Seeded default administrator '%s'", username)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _seed_admin()
    logger.info(
        "%s started (env=%s, model=%s)", settings.app_name, settings.app_env, settings.model_version
    )
    yield


app = FastAPI(
    title="Intelli-Credit (CreditDNA) API",
    description="AI-powered corporate credit decisioning engine for the Indian lending ecosystem.",
    version=settings.model_version,
    openapi_version="3.0.3",
    lifespan=lifespan,
)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    """Attach a correlation id to every request/response (observability)."""
    import uuid as _uuid

    request_id = request.headers.get("x-request-id") or _uuid.uuid4().hex
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Return HTTP 400 with specific error details for malformed requests (Req 23.3)."""
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "code": "MALFORMED_REQUEST",
            "detail": jsonable_encoder(exc.errors()),
            "request_id": getattr(request.state, "request_id", None),
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Consistent JSON envelope for unexpected server errors."""
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "code": "INTERNAL_ERROR",
            "detail": "An unexpected error occurred.",
            "request_id": getattr(request.state, "request_id", None),
        },
    )


app.include_router(router)
# Versioned alias so clients can pin to /v1 (Requirement 23 hardening).
app.include_router(router, prefix="/v1")


@app.get("/", tags=["system"])
def root():
    return {
        "service": settings.app_name,
        "version": settings.model_version,
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


@app.get("/readyz", tags=["system"])
def readyz():
    """Readiness probe verifying database connectivity."""
    from sqlalchemy import text

    from config.database import engine

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not_ready"})


def run() -> None:  # pragma: no cover
    """Console entrypoint: launch the development server."""
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=bool(settings.debug))


if __name__ == "__main__":  # pragma: no cover
    run()
