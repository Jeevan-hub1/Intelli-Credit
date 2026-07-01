"""Coverage for audit chain, DB session, jobs, engine, CAM, and fraud edges."""

import time

import pytest

from config.database import init_db, session_scope
from services import audit


@pytest.fixture(scope="module", autouse=True)
def _db():
    init_db()


def test_audit_chain_and_verify_and_denied():
    with session_scope() as db:
        audit.record_audit(db, user_id="u1", action="a1")
        audit.record_audit(db, user_id="u2", action="a2")
        audit.record_access_denied(db, user_id="u3", action="x", reason="nope")
        assert audit.verify_chain(db) is True


def test_audit_chain_detects_tampering():

    with session_scope() as db:
        entry = audit.record_audit(db, user_id="tamper", action="orig")
        entry.action = "tampered"
        db.add(entry)
        db.commit()
        assert audit.verify_chain(db) is False


def test_session_scope_rollback_on_error():
    with pytest.raises(RuntimeError):
        with session_scope():
            raise RuntimeError("boom")


# --------------------------------- jobs -----------------------------------


def test_job_manager_success_and_failure():
    from services.jobs import JobState, job_manager

    ok = job_manager.submit(lambda: 42)
    bad = job_manager.submit(lambda: (_ for _ in ()).throw(ValueError("x")))
    for _ in range(50):
        j1, j2 = job_manager.get(ok), job_manager.get(bad)
        if j1.state in (JobState.COMPLETED, JobState.FAILED) and j2.state in (
            JobState.COMPLETED,
            JobState.FAILED,
        ):
            break
        time.sleep(0.05)
    assert job_manager.get(ok).state == JobState.COMPLETED
    assert job_manager.get(ok).result == 42
    assert job_manager.get(bad).state == JobState.FAILED
    assert job_manager.get(bad).as_dict()["error"]
    assert job_manager.get("nonexistent") is None


# ----------------------------- credit_engine ------------------------------


def test_engine_extract_features_with_gst_and_bank():
    from models.application import Application, Borrower, LoanRequest
    from services.credit_engine import AnalysisRequest, analyze

    app = Application(
        borrower=Borrower(name="Co", gstin="27AAPFU0939F1ZV", industry="mfg"),
        loan_request=LoanRequest(amount=1000, tenure_months=12),
    )
    req = AnalysisRequest(
        application=app,
        raw_financials=[
            {
                "fiscal_year": 2023,
                "balance_sheet": {"total_assets": 100, "total_liabilities": 60, "equity": 40},
                "profit_and_loss": {"revenue": 1000},
            }
        ],
        raw_gst_returns=[
            {
                "form_type": "GSTR-3B",
                "period": "2024-01",
                "gstin": "27AAPFU0939F1ZV",
                "total_taxable_value": 1000,
                "total_itc": 100,
            }
        ],
        raw_bank_statement={
            "account_number": "1",
            "transactions": [
                {"date": "2024-01-05", "description": "Salary", "credit": 500, "balance": 5000}
            ],
        },
    )
    result = analyze(req, persist_features=True)
    assert result.credit_score.overall_score >= 0
    assert result.feature_version >= 1


# ----------------------------- fraud_detector -----------------------------


def test_build_graph_from_bank_and_window_exclusion():
    from datetime import date

    from services.bank_parser import parse_bank_statement
    from services.fraud_detector import build_transaction_graph, detect_circular_trading

    bank = parse_bank_statement(
        {
            "account_number": "1",
            "transactions": [
                {
                    "date": "2024-01-01",
                    "description": "pay",
                    "debit": 100,
                    "balance": 0,
                    "counterparty": "B",
                },
                {
                    "date": "2024-01-02",
                    "description": "recv",
                    "credit": 100,
                    "balance": 100,
                    "counterparty": "B",
                },
                {"date": "2024-01-03", "description": "no cp", "debit": 50, "balance": 50},
            ],
        }
    )
    edges = build_transaction_graph(bank, self_entity="SELF")
    assert edges  # created from debit/credit with counterparty
    # A cycle spanning > 90 days must be excluded.
    far = [
        {"from": "A", "to": "B", "amount": 2_000_000, "date": date(2024, 1, 1)},
        {"from": "B", "to": "A", "amount": 2_000_000, "date": date(2024, 6, 1)},
    ]
    edges2 = build_transaction_graph(None, self_entity="A", extra_edges=far)
    findings, ratio = detect_circular_trading(edges2, total_revenue=10_000_000)
    assert ratio == 0.0  # excluded by 90-day window


def test_fake_itc_single_large_transaction():
    from models.gst import GSTData, GSTLineEntry, GSTReturn
    from services.fraud_detector import detect_fake_itc

    gst = GSTData(
        gstin="27A",
        returns=[
            GSTReturn(
                form_type="GSTR-2A",
                period="2024-01",
                gstin="27A",
                entries=[GSTLineEntry(gstin="27SUP", taxable_value=600000, invoice_count=1)],
            )
        ],
    )
    findings = detect_fake_itc(gst)
    assert any("single transaction" in f.description.lower() for f in findings)


# ------------------------------ cam_generator -----------------------------


def test_cam_data_gap_sections_and_deltas():
    from models.application import Application, Borrower, LoanRequest
    from services.cam_generator import CAMInputs, generate_cam
    from services.scoring import ScoringContext, synthesize

    app = Application(
        borrower=Borrower(name="Co", industry="mfg"),
        loan_request=LoanRequest(amount=1000, tenure_months=12),
    )
    score = synthesize(ScoringContext(loan_request=app.loan_request))
    cam1 = generate_cam(CAMInputs(application=app, credit_score=score))
    # No financials/gst/bank -> data-gap sections present.
    assert cam1.section("financial_analysis").flags == ["DATA_GAP"]
    assert cam1.section("banking_conduct").flags == ["DATA_GAP"]
    assert cam1.section("gst_analysis").flags == ["DATA_GAP"]
    # Regenerate with a changed score to produce version deltas.
    score.overall_score += 5
    cam2 = generate_cam(CAMInputs(application=app, credit_score=score), previous=cam1)
    assert cam2.version == 2
    assert any(d.field == "overall_score" for d in cam2.deltas_from_previous)
