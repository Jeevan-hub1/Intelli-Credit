"""Application settings loaded from environment / .env file."""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

try:
    from pydantic import Field
    from pydantic_settings import BaseSettings, SettingsConfigDict

    _HAS_PYDANTIC_SETTINGS = True
except Exception:  # pragma: no cover - fallback when dependency missing
    _HAS_PYDANTIC_SETTINGS = False


if _HAS_PYDANTIC_SETTINGS:

    class Settings(BaseSettings):
        """Strongly-typed application configuration."""

        model_config = SettingsConfigDict(
            env_file=".env", env_file_encoding="utf-8", extra="ignore"
        )

        # Application
        app_name: str = Field(default="Intelli-Credit", alias="APP_NAME")
        app_env: str = Field(default="development", alias="APP_ENV")
        debug: bool = Field(default=True, alias="DEBUG")
        model_version: str = Field(default="1.0.0", alias="MODEL_VERSION")

        # Security
        secret_key: str = Field(default="dev-insecure-secret-key", alias="SECRET_KEY")
        access_token_expire_minutes: int = Field(default=60, alias="ACCESS_TOKEN_EXPIRE_MINUTES")
        encryption_key: Optional[str] = Field(default=None, alias="ENCRYPTION_KEY")

        # Database
        database_url: Optional[str] = Field(default=None, alias="DATABASE_URL")

        # Storage
        storage_backend: str = Field(default="local", alias="STORAGE_BACKEND")
        storage_local_path: str = Field(default="./_storage", alias="STORAGE_LOCAL_PATH")
        minio_endpoint: str = Field(default="localhost:9000", alias="MINIO_ENDPOINT")
        minio_access_key: str = Field(default="minioadmin", alias="MINIO_ACCESS_KEY")
        minio_secret_key: str = Field(default="minioadmin", alias="MINIO_SECRET_KEY")
        minio_bucket: str = Field(default="intelli-credit", alias="MINIO_BUCKET")
        minio_secure: bool = Field(default=False, alias="MINIO_SECURE")

        # Databricks / Delta Lake
        databricks_enabled: bool = Field(default=False, alias="DATABRICKS_ENABLED")
        databricks_host: Optional[str] = Field(default=None, alias="DATABRICKS_HOST")
        databricks_token: Optional[str] = Field(default=None, alias="DATABRICKS_TOKEN")
        databricks_http_path: Optional[str] = Field(default=None, alias="DATABRICKS_HTTP_PATH")
        delta_feature_store_path: str = Field(
            default="./_delta_feature_store", alias="DELTA_FEATURE_STORE_PATH"
        )

        # External APIs
        mca21_api_url: str = Field(default="https://api.mca.gov.in", alias="MCA21_API_URL")
        mca21_api_key: Optional[str] = Field(default=None, alias="MCA21_API_KEY")
        ecourts_api_url: str = Field(
            default="https://services.ecourts.gov.in", alias="ECOURTS_API_URL"
        )
        ecourts_api_key: Optional[str] = Field(default=None, alias="ECOURTS_API_KEY")

        # Research agent
        research_agent_enabled: bool = Field(default=True, alias="RESEARCH_AGENT_ENABLED")
        research_agent_timeout_seconds: int = Field(
            default=120, alias="RESEARCH_AGENT_TIMEOUT_SECONDS"
        )

        # Rate limiting
        rate_limit_per_minute: int = Field(default=100, alias="RATE_LIMIT_PER_MINUTE")

        @property
        def effective_database_url(self) -> str:
            """Return configured DB URL or a local SQLite fallback."""
            return self.database_url or "sqlite:///./intelli_credit.db"

else:  # pragma: no cover - minimal fallback configuration object

    import os

    class Settings:  # type: ignore[no-redef]
        """Lightweight settings fallback when pydantic-settings is unavailable."""

        def __init__(self) -> None:
            self.app_name = os.getenv("APP_NAME", "Intelli-Credit")
            self.app_env = os.getenv("APP_ENV", "development")
            self.debug = os.getenv("DEBUG", "true").lower() == "true"
            self.model_version = os.getenv("MODEL_VERSION", "1.0.0")
            self.secret_key = os.getenv("SECRET_KEY", "dev-insecure-secret-key")
            self.access_token_expire_minutes = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
            self.encryption_key = os.getenv("ENCRYPTION_KEY") or None
            self.database_url = os.getenv("DATABASE_URL") or None
            self.storage_backend = os.getenv("STORAGE_BACKEND", "local")
            self.storage_local_path = os.getenv("STORAGE_LOCAL_PATH", "./_storage")
            self.minio_bucket = os.getenv("MINIO_BUCKET", "intelli-credit")
            self.databricks_enabled = os.getenv("DATABRICKS_ENABLED", "false").lower() == "true"
            self.delta_feature_store_path = os.getenv(
                "DELTA_FEATURE_STORE_PATH", "./_delta_feature_store"
            )
            self.mca21_api_url = os.getenv("MCA21_API_URL", "https://api.mca.gov.in")
            self.mca21_api_key = os.getenv("MCA21_API_KEY") or None
            self.ecourts_api_url = os.getenv("ECOURTS_API_URL", "https://services.ecourts.gov.in")
            self.ecourts_api_key = os.getenv("ECOURTS_API_KEY") or None
            self.research_agent_enabled = (
                os.getenv("RESEARCH_AGENT_ENABLED", "true").lower() == "true"
            )
            self.research_agent_timeout_seconds = int(
                os.getenv("RESEARCH_AGENT_TIMEOUT_SECONDS", "120")
            )
            self.rate_limit_per_minute = int(os.getenv("RATE_LIMIT_PER_MINUTE", "100"))

        @property
        def effective_database_url(self) -> str:
            return self.database_url or "sqlite:///./intelli_credit.db"


@lru_cache
def get_settings() -> "Settings":
    """Return a cached Settings instance."""
    return Settings()


settings = get_settings()
