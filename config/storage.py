"""Object storage abstraction for documents (MinIO / S3 / local).

Provides a uniform interface; defaults to an encrypted-at-rest local
filesystem backend so the system works without external object storage.
Encryption is handled by services.security before bytes reach storage.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Optional

from config.settings import settings
from utils.logging import get_logger

logger = get_logger(__name__)


class StorageBackend:
    """Interface for object storage backends."""

    def put(self, data: bytes, *, key: Optional[str] = None, content_type: str = "application/octet-stream") -> str:
        raise NotImplementedError

    def get(self, key: str) -> bytes:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError


class LocalStorage(StorageBackend):
    """Filesystem-backed storage rooted at settings.storage_local_path."""

    def __init__(self, root: Optional[str] = None) -> None:
        self.root = Path(root or settings.storage_local_path)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / key

    def put(self, data: bytes, *, key: Optional[str] = None, content_type: str = "application/octet-stream") -> str:
        key = key or f"{uuid.uuid4().hex}.bin"
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        logger.info("Stored object %s (%d bytes)", key, len(data))
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()



class MinioStorage(StorageBackend):
    """MinIO/S3 backend. Activated when the `minio` package is installed."""

    def __init__(self) -> None:
        from minio import Minio  # type: ignore

        self._client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        self._bucket = settings.minio_bucket
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)

    def put(self, data: bytes, *, key: Optional[str] = None, content_type: str = "application/octet-stream") -> str:
        import io

        key = key or f"{uuid.uuid4().hex}.bin"
        self._client.put_object(self._bucket, key, io.BytesIO(data), length=len(data), content_type=content_type)
        return key

    def get(self, key: str) -> bytes:
        resp = self._client.get_object(self._bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    def delete(self, key: str) -> None:
        self._client.remove_object(self._bucket, key)

    def exists(self, key: str) -> bool:
        try:
            self._client.stat_object(self._bucket, key)
            return True
        except Exception:
            return False


def get_storage() -> StorageBackend:
    """Return the configured storage backend, falling back to local."""
    backend = (settings.storage_backend or "local").lower()
    if backend in ("minio", "s3"):
        try:
            return MinioStorage()
        except Exception as exc:  # pragma: no cover - depends on optional dep/infra
            logger.warning("MinIO/S3 unavailable (%s); falling back to local storage", exc)
    return LocalStorage()


storage = get_storage()
