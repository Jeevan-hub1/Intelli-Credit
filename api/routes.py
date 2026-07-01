"""REST API routes for Intelli-Credit (Requirements 21-23, 26, 30)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from api import store
from api.deps import CurrentUser, get_current_user, oauth2_scheme, rate_limiter, require_role
from api.schemas import (
    AnalyzeRequest,
    ApplicationCreate,
    EWSCheckIn,
    FinalizeIn,
    OverrideIn,
    QualitativeNoteIn,
    RefreshRequest,
    ReportRequest,
    TokenResponse,
)
from config.database import get_db
from config.settings import settings
from config.storage import storage
from models.application import Application, Borrower, CollateralItem, LoanRequest
from models.base import ApplicationStatus, UserRole, score_to_risk_band
from models.db_models import DBApplication, DBIdempotencyKey, DBOverride, DBQualitativeNote, DBUser
from models.scoring import QualitativeNote
from services import cam_generator, document_parser, extraction, security
from services.audit import record_audit
from services.credit_engine import AnalysisRequest, analyze
from services.jobs import job_manager
from utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(dependencies=[Depends(rate_limiter)])

# Allowed upload content types (Requirement 1.1).
ALLOWED_UPLOAD_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "text/csv",
    "image/png",
    "image/jpeg",
    "image/tiff",
    "application/octet-stream",
}


@router.post("/auth/token", response_model=TokenResponse, tags=["auth"])
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """OAuth2 password grant -> JWT bearer token (Requirement 23.2)."""
    user = db.query(DBUser).filter(DBUser.username == form.username).first()
    if not user or not security.verify_password(form.password, user.password_hash):
        record_audit(
            db,
            user_id=user.id if user else None,
            action="login",
            success=False,
            detail={"username": form.username},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password"
        )
    token = security.create_access_token(subject=user.id, role=UserRole(user.role))
    refresh = security.create_refresh_token(subject=user.id, role=UserRole(user.role))
    record_audit(db, user_id=user.id, action="login", success=True)
    return TokenResponse(access_token=token, role=user.role, refresh_token=refresh)


@router.post("/auth/refresh", response_model=TokenResponse, tags=["auth"])
def refresh_token(body: RefreshRequest, db: Session = Depends(get_db)):
    """Exchange a valid refresh token for a new access token (Requirement 23.2)."""
    try:
        payload = security.decode_access_token(body.refresh_token, expected_type="refresh")
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token"
        )
    user = db.get(DBUser, payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown user")
    access = security.create_access_token(subject=user.id, role=UserRole(user.role))
    return TokenResponse(access_token=access, role=user.role, refresh_token=body.refresh_token)


@router.post("/auth/logout", tags=["auth"])
def logout(
    user: CurrentUser = Depends(get_current_user),
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    """Revoke the presented access token (Requirement 23.2)."""
    payload = security.decode_access_token(token)
    security.revoke_token(payload.get("jti", ""))
    record_audit(db, user_id=user.id, action="logout", success=True)
    return {"revoked": True}


def _build_application(body: ApplicationCreate) -> Application:
    borrower = Borrower(**body.borrower.model_dump())
    loan = LoanRequest(
        amount=body.loan_request.amount,
        tenure_months=body.loan_request.tenure_months,
        purpose=body.loan_request.purpose,
        collateral=[CollateralItem(**c.model_dump()) for c in body.loan_request.collateral],
    )
    return Application(borrower=borrower, loan_request=loan)


@router.post("/applications", tags=["applications"], status_code=201)
def create_application(
    body: ApplicationCreate,
    db: Session = Depends(get_db),
    idempotency_key: Optional[str] = Header(default=None),
    user: CurrentUser = Depends(require_role(UserRole.ANALYST)),
):
    """Create a new credit application (Requirement 23.1).

    Supplying an `Idempotency-Key` header makes retries safe: a repeated key
    returns the originally created application instead of a duplicate.
    """
    if idempotency_key:
        existing = db.get(DBIdempotencyKey, idempotency_key)
        if existing:
            return {
                "application_id": existing.resource_id,
                "status": "exists",
                "idempotent_replay": True,
            }
    app = _build_application(body)
    app.assigned_officer_id = user.id
    store.persist_application(db, app)
    if idempotency_key:
        db.add(DBIdempotencyKey(key=idempotency_key, user_id=user.id, resource_id=app.id))
        db.commit()
    record_audit(
        db,
        user_id=user.id,
        action="create_application",
        resource_type="application",
        resource_id=app.id,
    )
    return {"application_id": app.id, "status": app.status.value}


@router.get("/applications", tags=["applications"])
def list_applications(
    db: Session = Depends(get_db),
    status_filter: Optional[str] = None,
    industry: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    sort: str = "created_at_desc",
    user: CurrentUser = Depends(get_current_user),
):
    """List applications with filtering, sorting, and pagination (Req 23 hardening)."""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    q = db.query(DBApplication)
    if status_filter:
        q = q.filter(DBApplication.status == status_filter)
    if industry:
        q = q.filter(DBApplication.industry == industry)
    total = q.count()
    order = (
        DBApplication.created_at.asc()
        if sort == "created_at_asc"
        else DBApplication.created_at.desc()
    )
    rows = q.order_by(order).limit(limit).offset(offset).all()
    items = [
        {
            "application_id": r.id,
            "borrower_name": r.borrower_name,
            "industry": r.industry,
            "status": r.status,
            "loan_amount": r.loan_amount,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.post("/applications/{app_id}/documents", tags=["applications"])
async def upload_document(
    app_id: str,
    file: UploadFile = File(...),
    type_hint: str = Form(default=""),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_role(UserRole.ANALYST)),
):
    """Upload and classify a document; stored AES-256 encrypted (Req 1, 19.1)."""
    from models.base import DocumentType

    db_app = db.get(DBApplication, app_id)
    if not db_app:
        raise HTTPException(status_code=404, detail="Application not found")

    # Requirement 1.4: enforce a max upload size (10MB) and content-type allowlist.
    if file.content_type and file.content_type not in ALLOWED_UPLOAD_TYPES:
        raise HTTPException(
            status_code=415, detail=f"Unsupported content type '{file.content_type}'"
        )
    data = await file.read()
    if len(data) > document_parser.MAX_SYNC_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds the 10MB limit")

    hint = DocumentType(type_hint) if type_hint else None
    try:
        encrypted = security.encrypt_bytes(data)
        key = storage.put(encrypted, key=f"documents/{app_id}/{file.filename}")
        doc = document_parser.ingest_document(
            file.filename, data, extracted_text=_safe_text(data), type_hint=hint, storage_key=key
        )
    except document_parser.DocumentError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message})

    # Extract a structured record from the raw bytes so it can feed the parsers.
    extracted = extraction.extract_structured(doc.doc_type, file.filename, data)

    record_audit(
        db,
        user_id=user.id,
        action="upload_document",
        resource_type="document",
        resource_id=doc.id,
        detail={"doc_type": doc.doc_type.value, "confidence": doc.classification_confidence},
    )
    return {
        "document_id": doc.id,
        "doc_type": doc.doc_type.value,
        "classification_confidence": doc.classification_confidence,
        "needs_manual_review": doc.needs_manual_review,
        "flags": [f.code for f in doc.flags],
        "extraction": {"kind": extracted["kind"], "source": extracted["source"]},
    }


def _safe_text(data: bytes) -> str:
    try:
        return data.decode("utf-8", errors="ignore")[:5000]
    except Exception:  # pragma: no cover - errors='ignore' never raises
        return ""


def _analyze_and_persist(db: Session, app_id: str, body: AnalyzeRequest, generated_by: str) -> dict:
    """Run analysis, persist artifacts, and return a result summary."""
    db_app = db.get(DBApplication, app_id)
    if not db_app:  # pragma: no cover - guarded by run_analysis before dispatch
        raise HTTPException(status_code=404, detail="Application not found")
    app = Application.model_validate(db_app.payload)

    req = AnalysisRequest(application=app, **body.model_dump())
    result = analyze(req, model_version=settings.model_version, generated_by=generated_by)

    store.registry.put_result(result)
    store.persist_application(db, result.application)
    store.persist_analysis_input(
        db,
        app_id,
        body.model_dump(),
        model_version=settings.model_version,
        generated_by=generated_by,
    )
    store.persist_credit_score(db, app_id, result.credit_score)
    store.persist_cam(db, result.cam)
    record_audit(
        db,
        user_id=generated_by,
        action="run_analysis",
        resource_type="application",
        resource_id=app_id,
        detail={"score": result.credit_score.overall_score, "elapsed_s": result.elapsed_seconds},
    )
    cs = result.credit_score
    return {
        "application_id": app_id,
        "overall_score": cs.overall_score,
        "risk_band": cs.risk_band.value,
        "recommendation": cs.recommendation.value,
        "data_completeness": cs.data_completeness,
        "low_confidence": cs.low_confidence,
        "elapsed_seconds": result.elapsed_seconds,
        "cam_id": result.cam.id,
        "cam_version": result.cam.version,
    }


def _background_analysis(app_id: str, body: AnalyzeRequest, generated_by: str) -> dict:
    """Job entrypoint: run analysis inside its own DB session."""
    from config.database import session_scope

    with session_scope() as db:
        return _analyze_and_persist(db, app_id, body, generated_by)


@router.post("/applications/{app_id}/analyze", tags=["analysis"])
def run_analysis(
    app_id: str,
    body: AnalyzeRequest,
    async_mode: bool = False,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_role(UserRole.ANALYST)),
):
    """Run the full credit analysis pipeline, sync or async (Requirements 14, 20).

    With `async_mode=true` the analysis runs as a background job and the call
    returns 202 with a job id to poll at `GET /jobs/{job_id}`.
    """
    if not db.get(DBApplication, app_id):
        raise HTTPException(status_code=404, detail="Application not found")
    if async_mode:
        job_id = job_manager.submit(_background_analysis, app_id, body, user.id)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"job_id": job_id, "status": "accepted", "poll": f"/jobs/{job_id}"},
        )
    return _analyze_and_persist(db, app_id, body, user.id)


@router.get("/jobs/{job_id}", tags=["analysis"])
def get_job(job_id: str, user: CurrentUser = Depends(get_current_user)):
    """Poll the status/result of a background analysis job (Requirement 20.2)."""
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.as_dict()


@router.get("/applications/{app_id}/results", tags=["analysis"])
def get_results(
    app_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)
):
    """Retrieve the detailed analysis result (Requirement 23.1)."""
    score = store.load_credit_score(db, app_id)
    if not score:
        raise HTTPException(status_code=404, detail="No analysis found for this application")
    return score.model_dump(mode="json")


@router.get("/applications/{app_id}/cam", tags=["cam"])
def get_cam(
    app_id: str,
    version: int | None = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Get a CAM (latest or a specific version)."""
    versions = store.list_cam_versions(app_id, db)
    if not versions:
        raise HTTPException(status_code=404, detail="No CAM found")
    cam = (
        versions[-1]
        if version is None
        else next((c for c in versions if c.version == version), None)
    )
    if cam is None:
        raise HTTPException(status_code=404, detail=f"CAM version {version} not found")
    return cam.model_dump(mode="json")


@router.get("/applications/{app_id}/cam/versions", tags=["cam"])
def list_versions(
    app_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)
):
    """List all CAM versions for an application (Requirement 30.1)."""
    versions = store.list_cam_versions(app_id, db)
    return [
        {
            "version": c.version,
            "overall_score": c.overall_score,
            "recommendation": c.recommendation.value,
            "is_final": c.is_final,
            "generated_by": c.generated_by,
            "modification_reason": c.modification_reason,
        }
        for c in versions
    ]


@router.get("/applications/{app_id}/cam/compare", tags=["cam"])
def compare_versions(
    app_id: str,
    v1: int,
    v2: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Side-by-side comparison of two CAM versions (Requirement 30.5)."""
    versions = {c.version: c for c in store.list_cam_versions(app_id, db)}
    if v1 not in versions or v2 not in versions:
        raise HTTPException(status_code=404, detail="One or both versions not found")
    a, b = versions[v1], versions[v2]
    diffs = []
    b_secs = {s.key: s.body for s in b.sections}
    for s in a.sections:
        if b_secs.get(s.key) != s.body:
            diffs.append(
                {"section": s.key, "v1": s.body[:200], "v2": (b_secs.get(s.key) or "")[:200]}
            )
    return {
        "v1": v1,
        "v2": v2,
        "score_delta": round(b.overall_score - a.overall_score, 2),
        "section_diffs": diffs,
    }


@router.post("/applications/{app_id}/notes", tags=["review"])
def add_note(
    app_id: str,
    body: QualitativeNoteIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_role(UserRole.CREDIT_OFFICER)),
):
    """Submit a qualitative note; recomputes affected scores (Requirement 26)."""
    result = store.rehydrate(db, app_id)
    if not result:
        raise HTTPException(status_code=404, detail="Run analysis before adding notes")
    from services.scoring import apply_qualitative_notes, evaluate_qualitative_note

    note = QualitativeNote(
        officer_id=user.id,
        text=body.text,
        dimension=body.dimension,
        sentiment=body.sentiment,
        severity=body.severity,
    )
    note.score_impact = evaluate_qualitative_note(note)
    apply_qualitative_notes(result.credit_score.dimensions, [note])
    _recompute_overall(result.credit_score)

    db.add(
        DBQualitativeNote(
            application_id=app_id,
            officer_id=user.id,
            dimension=body.dimension.value if body.dimension else "",
            text=body.text,
            sentiment=body.sentiment.value,
            severity=body.severity.value,
            score_impact=note.score_impact,
        )
    )
    db.commit()
    record_audit(
        db,
        user_id=user.id,
        action="add_note",
        resource_type="application",
        resource_id=app_id,
        detail={"impact": note.score_impact},
    )
    return {"score_impact": note.score_impact, "overall_score": result.credit_score.overall_score}


def _recompute_overall(score) -> None:
    """Recompute overall score, band, criticals, and recommendation (Req 21.3)."""
    from services.scoring import recommend

    score.overall_score = round(sum(d.score * d.weight for d in score.dimensions), 2)
    score.risk_band = score_to_risk_band(score.overall_score)
    score.critical_weaknesses = [d.dimension for d in score.dimensions if d.is_critical_weakness]
    score.recommendation = recommend(score.overall_score, score.critical_weaknesses, None)


def _regenerate_cam(app_id: str, db: Session, user_id: str, reason: str):
    """Regenerate a new CAM version preserving prior versions (Req 30.2)."""
    result = store.rehydrate(db, app_id)
    prev = store.list_cam_versions(app_id, db)[-1]
    inp = cam_generator.CAMInputs(
        application=result.application,
        credit_score=result.credit_score,
        financials=result.scoring_context.financials,
        gst=result.scoring_context.gst,
        bank=result.scoring_context.bank,
        fraud=result.scoring_context.fraud,
        compliance=result.scoring_context.compliance,
        litigation=result.scoring_context.litigation,
        research=result.scoring_context.research,
    )
    cam = cam_generator.generate_cam(
        inp, previous=prev, generated_by=user_id, modification_reason=reason
    )
    store.registry.add_cam_version(app_id, cam)
    store.persist_cam(db, cam)
    return cam


@router.post("/applications/{app_id}/override", tags=["review"])
def override_score(
    app_id: str,
    body: OverrideIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_role(UserRole.CREDIT_OFFICER)),
):
    """Override a dimension score with justification (Requirement 21.2-21.4)."""
    result = store.rehydrate(db, app_id)
    if not result:
        raise HTTPException(status_code=404, detail="Run analysis before overriding")
    dim = result.credit_score.dimension(body.dimension)
    if not dim:  # pragma: no cover - all Five Cs dimensions are always present
        raise HTTPException(status_code=400, detail="Unknown dimension")
    old = dim.score
    dim.score = body.new_score
    dim.overridden = True
    dim.override_reason = body.reason
    dim.override_by = user.id
    dim.is_critical_weakness = dim.score < 30
    _recompute_overall(result.credit_score)
    cam = _regenerate_cam(app_id, db, user.id, f"Override {body.dimension.value}: {body.reason}")

    db.add(
        DBOverride(
            application_id=app_id,
            user_id=user.id,
            dimension=body.dimension.value,
            old_score=old,
            new_score=body.new_score,
            reason=body.reason,
        )
    )
    db.commit()
    record_audit(
        db,
        user_id=user.id,
        action="override_score",
        resource_type="application",
        resource_id=app_id,
        detail={"dimension": body.dimension.value, "old": old, "new": body.new_score},
    )
    return {
        "dimension": body.dimension.value,
        "old_score": old,
        "new_score": body.new_score,
        "overall_score": result.credit_score.overall_score,
        "recommendation": result.credit_score.recommendation.value,
        "cam_version": cam.version,
    }


@router.post("/applications/{app_id}/finalize", tags=["review"])
def finalize_cam(
    app_id: str,
    body: FinalizeIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_role(UserRole.CREDIT_OFFICER)),
):
    """Mark the latest CAM as final after officer approval (Requirement 21.5)."""
    versions = store.list_cam_versions(app_id, db)
    if not versions:
        raise HTTPException(status_code=404, detail="No CAM to finalize")
    cam = versions[-1]
    cam.is_final = True
    db_app = db.get(DBApplication, app_id)
    if db_app:
        application = Application.model_validate(db_app.payload)
        application.status = ApplicationStatus.CAM_FINALIZED
        store.persist_application(db, application)
        result = store.registry.results.get(app_id)
        if result:
            result.application.status = ApplicationStatus.CAM_FINALIZED
    store.persist_cam(db, cam)
    record_audit(
        db,
        user_id=user.id,
        action="finalize_cam",
        resource_type="cam",
        resource_id=cam.id,
        detail={"approve": body.approve, "reason": body.reason},
    )
    return {"cam_id": cam.id, "version": cam.version, "is_final": True}


@router.post("/ews/check", tags=["ews"])
def ews_check(
    body: EWSCheckIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_role(UserRole.CREDIT_OFFICER)),
):
    """Evaluate EWS triggers and produce a prioritized alert (Req 17, 18)."""
    from services.ews_monitor import detect_triggers, evaluate_alert

    triggers = detect_triggers(
        days_financials_overdue=body.days_financials_overdue,
        gst_filing_gaps=body.gst_filing_gaps,
        adverse_news_count=body.adverse_news_count,
        rating_downgraded=body.rating_downgraded,
    )
    alert = evaluate_alert(
        body.borrower_id,
        triggers,
        application_id=body.application_id,
        exposure_amount=body.exposure_amount,
        assigned_officer_id=body.assigned_officer_id or user.id,
    )
    from models.db_models import DBEWSAlert

    db.add(
        DBEWSAlert(
            borrower_id=alert.borrower_id,
            application_id=alert.application_id or "",
            severity=alert.severity.value,
            ews_score=alert.ews_score,
            exposure_amount=alert.exposure_amount,
            escalated=alert.escalated,
            assigned_officer_id=alert.assigned_officer_id or "",
            payload=alert.model_dump(mode="json"),
        )
    )
    db.commit()
    record_audit(
        db,
        user_id=user.id,
        action="ews_check",
        resource_type="borrower",
        resource_id=body.borrower_id,
        detail={"ews_score": alert.ews_score},
    )
    return alert.model_dump(mode="json")


@router.get("/ews/digest", tags=["ews"])
def ews_digest(
    top_n: int = 10,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_role(UserRole.CREDIT_OFFICER)),
):
    """Daily digest of top high-risk accounts (Requirement 18.4)."""
    from models.base import Severity
    from models.db_models import DBEWSAlert
    from models.ews import EWSAlert
    from services.ews_monitor import daily_digest

    rows = db.query(DBEWSAlert).all()
    alerts = [
        EWSAlert(
            borrower_id=r.borrower_id,
            application_id=r.application_id or None,
            severity=Severity(r.severity),
            ews_score=r.ews_score,
            exposure_amount=r.exposure_amount,
            escalated=r.escalated,
        )
        for r in rows
    ]
    return [d.model_dump(mode="json") for d in daily_digest(alerts, top_n=top_n)]


@router.post("/reports/portfolio", tags=["reports"])
def portfolio_report(
    body: ReportRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_role(UserRole.CREDIT_OFFICER)),
):
    """Generate monthly portfolio quality + sector exposure report (Req 22)."""
    from services.reports import PortfolioLoan, build_portfolio_report, export_excel, export_pdf

    loans = [PortfolioLoan(**loan.model_dump()) for loan in body.loans]
    report = build_portfolio_report(loans, period=body.period, capital_base=body.capital_base)
    excel_key = export_excel(report)
    pdf_key = export_pdf(report)
    record_audit(db, user_id=user.id, action="portfolio_report", resource_id=body.period)
    return {
        "period": report.period,
        "total_exposure": report.total_exposure,
        "npa_breakdown": report.npa_breakdown,
        "sector_exposure": report.sector_exposure,
        "exposure_limit_breaches": report.exposure_limit_breaches,
        "excel_key": excel_key,
        "pdf_key": pdf_key,
    }


@router.get("/healthz", tags=["system"])
def healthz():
    """Liveness probe (Requirement 20.6 support)."""
    return {"status": "ok", "app": settings.app_name, "model_version": settings.model_version}
