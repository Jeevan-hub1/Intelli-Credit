"""Lightweight background job runner for asynchronous analysis (Req 20.2).

Uses a thread pool so the API can return immediately (202 Accepted) and the
caller can poll job status. In production this interface would be backed by a
distributed queue (Celery/RQ/arq); the contract here is identical.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from models.base import utcnow
from utils.logging import get_logger

logger = get_logger(__name__)


class JobState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Job:
    id: str
    state: JobState = JobState.PENDING
    result: Any = None
    error: Optional[str] = None
    created_at: Any = field(default_factory=utcnow)
    updated_at: Any = field(default_factory=utcnow)

    def as_dict(self) -> dict:
        return {
            "job_id": self.id,
            "state": self.state.value,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class JobManager:
    """Tracks background jobs and runs them on a thread pool."""

    def __init__(self, max_workers: int = 4) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
        job = Job(id=uuid.uuid4().hex)
        with self._lock:
            self._jobs[job.id] = job
        self._executor.submit(self._run, job, func, args, kwargs)
        return job.id

    def _run(self, job: Job, func: Callable, args: tuple, kwargs: dict) -> None:
        job.state = JobState.RUNNING
        job.updated_at = utcnow()
        try:
            job.result = func(*args, **kwargs)
            job.state = JobState.COMPLETED
        except Exception as exc:  # noqa: BLE001 - surfaced via job.error
            job.error = str(exc)
            job.state = JobState.FAILED
            logger.exception("Background job %s failed", job.id)
        finally:
            job.updated_at = utcnow()

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)


job_manager = JobManager()
