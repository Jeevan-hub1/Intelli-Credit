"""Databricks Delta Lake feature store (Requirement 27).

Stores extracted features, scores, and model outputs with data lineage,
versioning for time-travel queries, and partitioning by application date and
borrower industry. Uses the Databricks SQL connector when configured and live;
otherwise persists to a local Delta-emulating versioned JSON store so the
unified pipeline functions in all environments.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config.databricks import databricks_config
from utils.logging import get_logger

logger = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class FeatureStore:
    """Append-only, versioned feature store with partitioning and lineage."""

    def __init__(self, root: Optional[str] = None) -> None:
        self.root = Path(root or databricks_config.feature_store_path)
        self.root.mkdir(parents=True, exist_ok=True)
        self._txn_log = self.root / "_delta_log.jsonl"

    def _partition_dir(self, application_date: str, borrower_industry: str) -> Path:
        # Partition layout per Requirement 27.5.
        safe_ind = (borrower_industry or "unclassified").replace("/", "_")
        p = self.root / f"application_date={application_date}" / f"borrower_industry={safe_ind}"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def write_features(
        self,
        *,
        application_id: str,
        features: dict[str, Any],
        application_date: str,
        borrower_industry: str,
        source: str = "credit_engine",
        mode: str = "batch",
    ) -> int:
        """Append a new feature version for an application (Req 27.1/27.2/27.4).

        Returns the new version number. Supports batch and streaming `mode`.
        """
        if databricks_config.is_live:  # pragma: no cover - requires live Databricks
            self._write_live(application_id, features, application_date, borrower_industry)

        part = self._partition_dir(application_date, borrower_industry)
        record_file = part / f"{application_id}.jsonl"
        version = self._next_version(application_id)
        record = {
            "application_id": application_id,
            "version": version,
            "ingested_at": _now_iso(),
            "mode": mode,
            "lineage": {"source": source, "partition": str(part.relative_to(self.root))},
            "features": features,
        }
        with record_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        self._append_txn_log("WRITE", application_id, version, str(record_file))
        logger.info("Feature store WRITE app=%s v=%d mode=%s", application_id, version, mode)
        return version

    def _next_version(self, application_id: str) -> int:
        versions = [
            r["version"]
            for r in self._read_txn_log()
            if r["action"] == "WRITE" and r["application_id"] == application_id
        ]
        return (max(versions) + 1) if versions else 1

    def read_features(
        self, application_id: str, *, version: Optional[int] = None
    ) -> Optional[dict]:
        """Read features; with `version` performs a time-travel query (Req 27.4)."""
        records = self._all_records(application_id)
        if not records:
            return None
        if version is not None:
            for r in records:
                if r["version"] == version:
                    return r
            return None
        return max(records, key=lambda r: r["version"])

    def history(self, application_id: str) -> list[dict]:
        """Return all versions for an application, ascending (time-travel index)."""
        return sorted(self._all_records(application_id), key=lambda r: r["version"])

    def _all_records(self, application_id: str) -> list[dict]:
        records: list[dict] = []
        for path in self.root.rglob(f"{application_id}.jsonl"):
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
        return records

    def _append_txn_log(self, action: str, application_id: str, version: int, path: str) -> None:
        entry = {
            "action": action,
            "application_id": application_id,
            "version": version,
            "path": path,
            "ts": _now_iso(),
        }
        with self._txn_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    def _read_txn_log(self) -> list[dict]:
        if not self._txn_log.exists():
            return []
        out = []
        with self._txn_log.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def _write_live(
        self, application_id, features, application_date, borrower_industry
    ) -> None:  # pragma: no cover - requires live Databricks
        """Write to a live Databricks Delta table (best-effort)."""
        try:
            from databricks import sql

            with sql.connect(
                server_hostname=databricks_config.host,
                http_path=databricks_config.http_path,
                access_token=databricks_config.token,
            ) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO credit_features "
                        "(application_id, application_date, borrower_industry, features, ingested_at) "
                        "VALUES (?, ?, ?, ?, current_timestamp())",
                        (
                            application_id,
                            application_date,
                            borrower_industry,
                            json.dumps(features, default=str),
                        ),
                    )
            logger.info("Feature store wrote to live Databricks for %s", application_id)
        except Exception as exc:
            logger.warning("Live Databricks write failed (%s); kept local copy", exc)


feature_store = FeatureStore()
