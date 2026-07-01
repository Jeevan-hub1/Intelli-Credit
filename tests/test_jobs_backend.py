"""Tests for the pluggable job backend."""

import time

from services.jobs import (
    JobManager,
    JobState,
    LocalThreadJobBackend,
    select_backend,
)


def _wait(backend, job_id, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = backend.get(job_id)
        if job and job.state in (JobState.COMPLETED, JobState.FAILED):
            return job
        time.sleep(0.02)
    return backend.get(job_id)


def test_local_backend_success_and_failure():
    backend = LocalThreadJobBackend(max_workers=2)
    ok = backend.submit(lambda a, b: a + b, 2, 3)
    bad = backend.submit(lambda: (_ for _ in ()).throw(ValueError("boom")))
    assert _wait(backend, ok).result == 5
    failed = _wait(backend, bad)
    assert failed.state == JobState.FAILED and "boom" in failed.error
    assert backend.get("unknown") is None


def test_select_backend_defaults_to_local():
    assert isinstance(select_backend(), LocalThreadJobBackend)


def test_job_manager_facade_delegates_and_swaps():
    jm = JobManager()
    assert isinstance(jm.backend, LocalThreadJobBackend)
    jid = jm.submit(lambda: 99)
    assert _wait(jm, jid).result == 99

    replacement = LocalThreadJobBackend(max_workers=1)
    jm.set_backend(replacement)
    assert jm.backend is replacement
    assert jm.get("nope") is None


def test_job_as_dict_shape():
    backend = LocalThreadJobBackend()
    jid = backend.submit(lambda: "x")
    d = _wait(backend, jid).as_dict()
    assert set(d) == {"job_id", "state", "result", "error", "created_at", "updated_at"}
