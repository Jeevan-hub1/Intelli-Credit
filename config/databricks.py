"""Databricks / Delta Lake configuration (Requirement 27).

Centralises connection details and exposes a `databricks_config` object.
When Databricks is disabled (default), the feature store service uses a
local Delta-emulating parquet/JSON store so the pipeline still functions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from config.settings import settings


@dataclass(frozen=True)
class DatabricksConfig:
    """Connection and layout configuration for the Delta Lake feature store."""

    enabled: bool
    host: Optional[str]
    token: Optional[str]
    http_path: Optional[str]
    feature_store_path: str

    # Partition layout per Requirement 27.5: by application date + industry.
    partition_columns: tuple[str, ...] = ("application_date", "borrower_industry")

    @property
    def is_live(self) -> bool:
        """True only when fully configured for a live Databricks connection."""
        return bool(self.enabled and self.host and self.token and self.http_path)


databricks_config = DatabricksConfig(
    enabled=settings.databricks_enabled,
    host=settings.databricks_host if hasattr(settings, "databricks_host") else None,
    token=settings.databricks_token if hasattr(settings, "databricks_token") else None,
    http_path=settings.databricks_http_path if hasattr(settings, "databricks_http_path") else None,
    feature_store_path=settings.delta_feature_store_path,
)
