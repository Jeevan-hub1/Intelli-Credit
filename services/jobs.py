"""Background job runner with a pluggable backend (Requirement 20.2).

`job_manager` is a thin facade that delegates to a configured `JobBackend`.
The default `LocalThreadJobBackend` runs jobs on an in-process thread pool.
Setting ``JOB_BACKEND=celery`` with a broker activates `CeleryJobBackend`
(distributed workers) - the facade API (`submit` / `get`) is identical, so
callers never change.
"""

from __future__ import annotations

import threading
import uuid
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from config.settings import settings
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


class JobBackend(ABC):
    """Interface for background job execution backends."""

    @abstractmethod
    def submit(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
        """Enqueue a job and return its id."""

    @abstractmethod
    def get(self, job_id: str) -> Optional[Job]:
        """Return the job's status/result, or None if unknown."""


class LocalThreadJobBackend(JobBackend):
    """In-process backend running jobs on a thread pool (default)."""

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


class CeleryJobBackend(JobBackend):  # pragma: no cover - requires celery + a live broker
    """Distributed backend backed by Celery workers.

    Enqueues work onto a broker (e.g. Redis) so jobs run in separate worker
    processes. Activated when ``JOB_BACKEND=celery`` and a broker is configured.
    """

    def __init__(self, broker_url: str, result_backend: Optional[str] = None) -> None:
        from celery import Celery

        self._app = Celery(
            "intelli_credit", broker=broker_url, backend=result_backend or broker_url
        )
        self._task = self._app.task(name="intelli_credit.run_job")(self._dispatch)

    @staticmethod
    def _dispatch(dotted_path: str, *args: Any, **kwargs: Any) -> Any:
        import importlib

        module, _, attr = dotted_path.rpartition(".")
        func = getattr(importlib.import_module(module), attr)
        return func(*args, **kwargs)

    def submit(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
        dotted = f"{func.__module__}.{func.__qualname__}"
        async_result = self._task.delay(dotted, *args, **kwargs)
        return async_result.id

    def get(self, job_id: str) -> Optional[Job]:
        res = self._app.AsyncResult(job_id)
        state_map = {
            "PENDING": JobState.PENDING,
            "STARTED": JobState.RUNNING,
            "SUCCESS": JobState.COMPLETED,
            "FAILURE": JobState.FAILED,
        }
        job = Job(id=job_id, state=state_map.get(res.state, JobState.PENDING))
        if res.successful():
            job.result = res.result
        elif res.failed():
            job.error = str(res.result)
        return job


def select_backend() -> JobBackend:
    """Choose a job backend from configuration (Celery when a broker is set)."""
    backend = getattr(settings, "job_backend", "local").lower()
    broker = getattr(settings, "celery_broker_url", None)
    if backend == "celery" and broker:  # pragma: no cover - requires celery + broker
        try:
            return CeleryJobBackend(broker, getattr(settings, "celery_result_backend", None))
        except Exception as exc:
            logger.warning("Celery backend unavailable (%s); using local thread pool", exc)
    return LocalThreadJobBackend()


class JobManager:
    """Facade delegating to the active job backend (swappable at runtime)."""

    def __init__(self, backend: Optional[JobBackend] = None) -> None:
        self._backend = backend or select_backend()

    def set_backend(self, backend: JobBackend) -> None:
        """Replace the active backend (e.g. to switch to Celery in tests/prod)."""
        self._backend = backend

    @property
    def backend(self) -> JobBackend:
        return self._backend

    def submit(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
        return self._backend.submit(func, *args, **kwargs)

    def get(self, job_id: str) -> Optional[Job]:
        return self._backend.get(job_id)


job_manager = JobManager()
