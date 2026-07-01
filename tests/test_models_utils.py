"""Edge-case coverage for models and utilities."""

import logging

from models.application import Application, Borrower, Document, LoanRequest
from models.base import (
    DocumentFormat,
    FiveCDimension,
    RiskBand,
    score_to_risk_band,
)
from models.cam import CAM
from models.financial import BalanceSheet, ProfitAndLoss
from models.gst import GSTLineEntry
from models.scoring import CreditScore, DimensionScore
from utils.logging import get_logger, log_context
from utils.temporal import DataType, decay_weight, weighted_average


def test_score_to_risk_band_all_bands():
    assert score_to_risk_band(90) == RiskBand.EXCELLENT
    assert score_to_risk_band(70) == RiskBand.GOOD
    assert score_to_risk_band(55) == RiskBand.FAIR
    assert score_to_risk_band(40) == RiskBand.POOR
    assert score_to_risk_band(10) == RiskBand.HIGH_RISK


def test_balance_sheet_edges():
    empty = BalanceSheet(fiscal_year=2023)
    assert empty.balances()  # 0 == 0
    pl = ProfitAndLoss(fiscal_year=2023, revenue=0)
    assert pl.ebitda_margin == 0.0


def test_gst_line_total_tax():
    e = GSTLineEntry(gstin="27A", igst=10, cgst=5, sgst=5)
    assert e.total_tax == 20


def test_scoring_dimension_lookup():
    cs = CreditScore(dimensions=[DimensionScore(dimension=FiveCDimension.CHARACTER, score=50)])
    assert cs.dimension(FiveCDimension.CHARACTER) is not None
    assert cs.dimension(FiveCDimension.CAPITAL) is None


def test_cam_section_lookup():
    cam = CAM(id="c1", application_id="a1")
    assert cam.section("missing") is None


def test_application_add_document():
    app = Application(
        borrower=Borrower(name="X"), loan_request=LoanRequest(amount=1, tenure_months=1)
    )
    app.add_document(Document(filename="f.pdf", fmt=DocumentFormat.PDF))
    assert len(app.documents) == 1
    assert app.status.value == "documents_uploaded"


def test_weighted_average_mismatch_raises():
    import pytest

    with pytest.raises(ValueError):
        weighted_average([1, 2], [1])


def test_decay_weight_zero_age():
    from datetime import datetime, timezone

    ref = datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert decay_weight(DataType.GST, ref, reference=ref) == 1.0


def test_log_context_and_exception(caplog):
    logger = get_logger("test.logger")
    with caplog.at_level(logging.INFO):
        log_context(logger, logging.INFO, "hello", key="value")
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("captured")


def test_local_storage_delete_and_exists(tmp_path):
    from config.storage import LocalStorage

    st = LocalStorage(root=str(tmp_path / "s"))
    key = st.put(b"hello", key="a/b.bin")
    assert st.exists(key)
    assert st.get(key) == b"hello"
    st.delete(key)
    assert not st.exists(key)
    st.delete(key)  # deleting a missing key is a no-op
