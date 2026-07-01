"""Smoke test that Alembic migrations build and tear down the full schema.

Migration modules are excluded from coverage (they run in a subprocess), but
this guards that `alembic upgrade head` / `downgrade base` actually work.
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _run_alembic(db_url: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DATABASE_URL"] = db_url
    env.setdefault("SECRET_KEY", "ci-test-secret-key-which-is-long-enough-1234567890")
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_alembic_upgrade_and_downgrade(tmp_path):
    db_file = tmp_path / "alembic_it.db"
    url = f"sqlite:///{db_file}"

    up = _run_alembic(url, "upgrade", "head")
    assert up.returncode == 0, up.stderr

    conn = sqlite3.connect(db_file)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    for expected in ("applications", "users", "audit_logs", "cams", "idempotency_keys"):
        assert expected in tables

    down = _run_alembic(url, "downgrade", "base")
    assert down.returncode == 0, down.stderr
