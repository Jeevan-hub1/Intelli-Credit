"""Intelli-Credit (CreditDNA) FastAPI application entrypoint."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
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
        db.add(DBUser(username=username, email="admin@intelli-credit.local",
                      role=UserRole.ADMINISTRATOR.value,
                      password_hash=security.hash_password(password)))
        logger.info("Seeded default administrator '%s'", username)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _seed_admin()
    logger.info("%s started (env=%s, model=%s)", settings.app_name,
                settings.app_env, settings.model_version)
    yield



app = FastAPI(
    title="Intelli-Credit (CreditDNA) API",
    description="AI-powered corporate credit decisioning engine for the Indian lending ecosystem.",
    version=settings.model_version,
    openapi_version="3.1.0",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Return HTTP 400 with specific error details for malformed requests (Req 23.3)."""
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"code": "MALFORMED_REQUEST", "detail": exc.errors()},
    )


app.include_router(router)


@app.get("/", tags=["system"])
def root():
    return {"service": settings.app_name, "version": settings.model_version,
            "docs": "/docs", "openapi": "/openapi.json"}


def run() -> None:
    """Console entrypoint: launch the development server."""
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=bool(settings.debug))


if __name__ == "__main__":
    run()
