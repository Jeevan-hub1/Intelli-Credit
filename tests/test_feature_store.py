"""Unit tests for the Delta feature store (Requirement 27)."""
import uuid

from services.feature_store import FeatureStore


def _store(tmp_path):
    return FeatureStore(root=str(tmp_path / "fs"))


def test_write_and_read_latest(tmp_path):
    fs = _store(tmp_path)
    app_id = f"app_{uuid.uuid4().hex[:8]}"
    v1 = fs.write_features(application_id=app_id, features={"overall_score": 70.0},
                           application_date="2026-06-01", borrower_industry="manufacturing")
    assert v1 == 1
    latest = fs.read_features(app_id)
    assert latest["features"]["overall_score"] == 70.0
    assert latest["version"] == 1


def test_versioning_and_time_travel(tmp_path):
    fs = _store(tmp_path)
    app_id = f"app_{uuid.uuid4().hex[:8]}"
    fs.write_features(application_id=app_id, features={"overall_score": 70.0},
                      application_date="2026-06-01", borrower_industry="mfg")
    v2 = fs.write_features(application_id=app_id, features={"overall_score": 75.0},
                           application_date="2026-06-01", borrower_industry="mfg")
    assert v2 == 2
    # Time-travel to v1 returns the original value.
    assert fs.read_features(app_id, version=1)["features"]["overall_score"] == 70.0
    assert fs.read_features(app_id, version=2)["features"]["overall_score"] == 75.0
    # Latest reflects v2.
    assert fs.read_features(app_id)["features"]["overall_score"] == 75.0
    history = fs.history(app_id)
    assert [h["version"] for h in history] == [1, 2]


def test_partitioning_and_lineage(tmp_path):
    fs = _store(tmp_path)
    app_id = f"app_{uuid.uuid4().hex[:8]}"
    fs.write_features(application_id=app_id, features={"x": 1}, application_date="2026-06-01",
                      borrower_industry="retail", source="unit_test")
    rec = fs.read_features(app_id)
    assert rec["lineage"]["source"] == "unit_test"
    assert "application_date=2026-06-01" in rec["lineage"]["partition"]
    assert "borrower_industry=retail" in rec["lineage"]["partition"]


def test_missing_application_returns_none(tmp_path):
    fs = _store(tmp_path)
    assert fs.read_features("does_not_exist") is None
