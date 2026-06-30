"""REST API routes for Intelli-Credit (Requirements 21-23, 26, 30)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from api import store
from api.deps import CurrentUser, get_current_user, rate_limiter, require_role
from api.schemas import (
    AnalyzeRequest,
    ApplicationCreate,
    EWSCheckIn,
    FinalizeIn,
    OverrideIn,
    QualitativeNoteIn,
    ReportRequest,
    TokenResponse,
)
from config.database import get_db
from config.settings import settings
from config.storage import storage
from models.application import Application, Borrower, CollateralItem, LoanRequest
from models.base import ApplicationStatus, FiveCDimension, UserRole, score_to_risk_band
from models.db_models import DBApplication, DBOverride, DBQualitativeNote, DBUser
from models.scoring import QualitativeNote
from services import cam_generator, document_parser, security
from services.audit import record_audit
from services.credit_engine import AnalysisRequest, analyze
from utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(dependencies=[Depends(rate_limiter)])


@router.post("/auth/token", response_model=TokenResponse, tags=["auth"])
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """OAuth2 password grant -> JWT bearer token (Requirement 23.2)."""
    user = db.query(DBUser).filter(DBUser.username == form.username).first()
    if not user or not security.verify_password(form.password, user.password_hash):
        record_audit(db, user_id=user.id if user else None, action="login",
                     success=False, detail={"username": form.username})
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Incorrect username or password")
    token = security.create_access_token(subject=user.id, role=UserRole(user.role))
    record_audit(db, user_id=user.id, action="login", success=True)
    return TokenResponse(access_token=token, role=user.role)



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
def create_application(body: ApplicationCreate, db: Session = Depends(get_db),
                       user: CurrentUser = Depends(require_role(UserRole.ANALYST))):
    """Create a new credit application (Requirement 23.1)."""
    app = _build_application(body)
    app.assigned_officer_id = user.id
    store.persist_application(db, app)
    record_audit(db, user_id=user.id, action="create_application",
                 resource_type="application", resource_id=app.id)
    return {"application_id": app.id, "status": app.status.value}


@router.post("/applications/{app_id}/documents", tags=["applications"])
async def upload_document(app_id: str, file: UploadFile = File(...),
                          type_hint: str = Form(default=""),
                          db: Session = Depends(get_db),
                          user: CurrentUser = Depends(require_role(UserRole.ANALYST))):
    """Upload and classify a document; stored AES-256 encrypted (Req 1, 19.1)."""
    from models.base import DocumentType

    db_app = db.get(DBApplication, app_id)
    if not db_app:
        raise HTTPException(status_code=404, detail="Application not found")
    data = await file.read()
    hint = DocumentType(type_hint) if type_hint else None
    try:
        encrypted = security.encrypt_bytes(data)
        key = storage.put(encrypted, key=f"documents/{app_id}/{file.filename}")
        doc = document_parser.ingest_document(
            file.filename, data, extracted_text=_safe_text(data),
            type_hint=hint, storage_key=key)
    except document_parser.DocumentError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message})
    record_audit(db, user_id=user.id, action="upload_document",
                 resource_type="document", resource_id=doc.id,
                 detail={"doc_type": doc.doc_type.value, "confidence": doc.classification_confidence})
    return {"document_id": doc.id, "doc_type": doc.doc_type.value,
            "classification_confidence": doc.classification_confidence,
            "needs_manual_review": doc.needs_manual_review,
            "flags": [f.code for f in doc.flags]}


def _safe_text(data: bytes) -> str:
    try:
        return data.decode("utf-8", errors="ignore")[:5000]
    except Exception:
        return ""



@router.post("/applications/{app_id}/analyze", tags=["analysis"])
def run_analysis(app_id: str, body: AnalyzeRequest, db: Session = Depends(get_db),
                 user: CurrentUser = Depends(require_role(UserRole.ANALYST))):
    """Run the full credit analysis pipeline (Requirements 14, 20)."""
    db_app = db.get(DBApplication, app_id)
    if not db_app:
        raise HTTPException(status_code=404, detail="Application not found")
    app = Application.model_validate(db_app.payload)

    req = AnalysisRequest(application=app, **body.model_dump())
    result = analyze(req, model_version=settings.model_version, generated_by=user.id)

    store.registry.put_result(result)
    store.persist_application(db, result.application)
    store.persist_credit_score(db, app_id, result.credit_score)
    store.persist_cam(db, result.cam)
    record_audit(db, user_id=user.id, action="run_analysis", resource_type="application",
                 resource_id=app_id, detail={"score": result.credit_score.overall_score,
                                             "elapsed_s": result.elapsed_seconds})
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


@router.get("/applications/{app_id}/results", tags=["analysis"])
def get_results(app_id: str, user: CurrentUser = Depends(get_current_user)):
    """Retrieve the detailed analysis result (Requirement 23.1)."""
    result = store.load_result(app_id)
    if not result:
        raise HTTPException(status_code=404, detail="No analysis found for this application")
    return result.credit_score.model_dump(mode="json")


@router.get("/applications/{app_id}/cam", tags=["cam"])
def get_cam(app_id: str, version: int | None = None,
            user: CurrentUser = Depends(get_current_user)):
    """Get a CAM (latest or a specific version)."""
    versions = store.list_cam_versions(app_id)
    if not versions:
        raise HTTPException(status_code=404, detail="No CAM found")
    cam = versions[-1] if version is None else next((c for c in versions if c.version == version), None)
    if cam is None:
        raise HTTPException(status_code=404, detail=f"CAM version {version} not found")
    return cam.model_dump(mode="json")



@router.get("/applications/{app_id}/cam/versions", tags=["cam"])
def list_versions(app_id: str, user: CurrentUser = Depends(get_current_user)):
    """List all CAM versions for an application (Requirement 30.1)."""
    versions = store.list_cam_versions(app_id)
    return [{"version": c.version, "overall_score": c.overall_score,
             "recommendation": c.recommendation.value, "is_final": c.is_final,
             "generated_by": c.generated_by, "modification_reason": c.modification_reason}
            for c in versions]


@router.get("/applications/{app_id}/cam/compare", tags=["cam"])
def compare_versions(app_id: str, v1: int, v2: int,
                     user: CurrentUser = Depends(get_current_user)):
    """Side-by-side comparison of two CAM versions (Requirement 30.5)."""
    versions = {c.version: c for c in store.list_cam_versions(app_id)}
    if v1 not in versions or v2 not in versions:
        raise HTTPException(status_code=404, detail="One or both versions not found")
    a, b = versions[v1], versions[v2]
    diffs = []
    b_secs = {s.key: s.body for s in b.sections}
    for s in a.sections:
        if b_secs.get(s.key) != s.body:
            diffs.append({"section": s.key, "v1": s.body[:200], "v2": (b_secs.get(s.key) or "")[:200]})
    return {"v1": v1, "v2": v2, "score_delta": round(b.overall_score - a.overall_score, 2),
            "section_diffs": diffs}


@router.post("/applications/{app_id}/notes", tags=["review"])
def add_note(app_id: str, body: QualitativeNoteIn, db: Session = Depends(get_db),
             user: CurrentUser = Depends(require_role(UserRole.CREDIT_OFFICER))):
    """Submit a qualitative note; recomputes affected scores (Requirement 26)."""
    result = store.load_result(app_id)
    if not result:
        raise HTTPException(status_code=404, detail="Run analysis before adding notes")
    from services.scoring import apply_qualitative_notes, evaluate_qualitative_note

    note = QualitativeNote(officer_id=user.id, text=body.text, dimension=body.dimension,
                           sentiment=body.sentiment, severity=body.severity)
    note.score_impact = evaluate_qualitative_note(note)
    apply_qualitative_notes(result.credit_score.dimensions, [note])
    _recompute_overall(result.credit_score)

    db.add(DBQualitativeNote(application_id=app_id, officer_id=user.id,
                             dimension=body.dimension.value if body.dimension else "",
                             text=body.text, sentiment=body.sentiment.value,
                             severity=body.severity.value, score_impact=note.score_impact))
    db.commit()
    record_audit(db, user_id=user.id, action="add_note", resource_type="application",
                 resource_id=app_id, detail={"impact": note.score_impact})
    return {"score_impact": note.score_impact, "overall_score": result.credit_score.overall_score}



def _recompute_overall(score) -> None:
    """Recompute overall score, band, criticals, and recommendation (Req 21.3)."""
    from models.base import FIVE_C_WEIGHTS
    from services.scoring import recommend

    score.overall_score = round(sum(d.score * d.weight for d in score.dimensions), 2)
    score.risk_band = score_to_risk_band(score.overall_score)
    score.critical_weaknesses = [d.dimension for d in score.dimensions if d.is_critical_weakness]
    score.recommendation = recommend(score.overall_score, score.critical_weaknesses, None)


def _regenerate_cam(app_id: str, db: Session, user_id: str, reason: str):
    """Regenerate a new CAM version preserving prior versions (Req 30.2)."""
    result = store.load_result(app_id)
    prev = store.list_cam_versions(app_id)[-1]
    inp = cam_generator.CAMInputs(
        application=result.application, credit_score=result.credit_score,
        financials=result.scoring_context.financials, gst=result.scoring_context.gst,
        bank=result.scoring_context.bank, fraud=result.scoring_context.fraud,
        compliance=result.scoring_context.compliance,
        litigation=result.scoring_context.litigation,
        research=result.scoring_context.research)
    cam = cam_generator.generate_cam(inp, previous=prev, generated_by=user_id,
                                     modification_reason=reason)
    store.registry.add_cam_version(app_id, cam)
    store.persist_cam(db, cam)
    return cam


@router.post("/applications/{app_id}/override", tags=["review"])
def override_score(app_id: str, body: OverrideIn, db: Session = Depends(get_db),
                   user: CurrentUser = Depends(require_role(UserRole.CREDIT_OFFICER))):
    """Override a dimension score with justification (Requirement 21.2-21.4)."""
    result = store.load_result(app_id)
    if not result:
        raise HTTPException(status_code=404, detail="Run analysis before overriding")
    dim = result.credit_score.dimension(body.dimension)
    if not dim:
        raise HTTPException(status_code=400, detail="Unknown dimension")
    old = dim.score
    dim.score = body.new_score
    dim.overridden = True
    dim.override_reason = body.reason
    dim.override_by = user.id
    dim.is_critical_weakness = dim.score < 30
    _recompute_overall(result.credit_score)
    cam = _regenerate_cam(app_id, db, user.id, f"Override {body.dimension.value}: {body.reason}")

    db.add(DBOverride(application_id=app_id, user_id=user.id, dimension=body.dimension.value,
                      old_score=old, new_score=body.new_score, reason=body.reason))
    db.commit()
    record_audit(db, user_id=user.id, action="override_score", resource_type="application",
                 resource_id=app_id, detail={"dimension": body.dimension.value,
                                             "old": old, "new": body.new_score})
    return {"dimension": body.dimension.value, "old_score": old, "new_score": body.new_score,
            "overall_score": result.credit_score.overall_score,
            "recommendation": result.credit_score.recommendation.value,
            "cam_version": cam.version}



@router.post("/applications/{app_id}/finalize", tags=["review"])
def finalize_cam(app_id: str, body: FinalizeIn, db: Session = Depends(get_db),
                 user: CurrentUser = Depends(require_role(UserRole.CREDIT_OFFICER))):
    """Mark the latest CAM as final after officer approval (Requirement 21.5)."""
    versions = store.list_cam_versions(app_id)
    if not versions:
        raise HTTPException(status_code=404, detail="No CAM to finalize")
    cam = versions[-1]
    cam.is_final = True
    result = store.load_result(app_id)
    if result:
        result.application.status = (
            ApplicationStatus.APPROVED if body.approve else ApplicationStatus.REJECTED)
        result.application.status = ApplicationStatus.CAM_FINALIZED
        store.persist_application(db, result.application)
    store.persist_cam(db, cam)
    record_audit(db, user_id=user.id, action="finalize_cam", resource_type="cam",
                 resource_id=cam.id, detail={"approve": body.approve, "reason": body.reason})
    return {"cam_id": cam.id, "version": cam.version, "is_final": True}



@router.post("/ews/check", tags=["ews"])
def ews_check(body: EWSCheckIn, db: Session = Depends(get_db),
              user: CurrentUser = Depends(require_role(UserRole.CREDIT_OFFICER))):
    """Evaluate EWS triggers and produce a prioritized alert (Req 17, 18)."""
    from services.ews_monitor import detect_triggers, evaluate_alert

    triggers = detect_triggers(
        days_financials_overdue=body.days_financials_overdue,
        gst_filing_gaps=body.gst_filing_gaps,
        adverse_news_count=body.adverse_news_count,
        rating_downgraded=body.rating_downgraded)
    alert = evaluate_alert(body.borrower_id, triggers, application_id=body.application_id,
                           exposure_amount=body.exposure_amount,
                           assigned_officer_id=body.assigned_officer_id or user.id)
    from models.db_models import DBEWSAlert
    db.add(DBEWSAlert(borrower_id=alert.borrower_id, application_id=alert.application_id or "",
                      severity=alert.severity.value, ews_score=alert.ews_score,
                      exposure_amount=alert.exposure_amount, escalated=alert.escalated,
                      assigned_officer_id=alert.assigned_officer_id or "",
                      payload=alert.model_dump(mode="json")))
    db.commit()
    record_audit(db, user_id=user.id, action="ews_check", resource_type="borrower",
                 resource_id=body.borrower_id, detail={"ews_score": alert.ews_score})
    return alert.model_dump(mode="json")


@router.get("/ews/digest", tags=["ews"])
def ews_digest(top_n: int = 10, db: Session = Depends(get_db),
               user: CurrentUser = Depends(require_role(UserRole.CREDIT_OFFICER))):
    """Daily digest of top high-risk accounts (Requirement 18.4)."""
    from models.base import Severity
    from models.ews import EWSAlert
    from models.db_models import DBEWSAlert
    from services.ews_monitor import daily_digest

    rows = db.query(DBEWSAlert).all()
    alerts = [EWSAlert(borrower_id=r.borrower_id, application_id=r.application_id or None,
                       severity=Severity(r.severity), ews_score=r.ews_score,
                       exposure_amount=r.exposure_amount, escalated=r.escalated)
              for r in rows]
    return [d.model_dump(mode="json") for d in daily_digest(alerts, top_n=top_n)]


@router.post("/reports/portfolio", tags=["reports"])
def portfolio_report(body: ReportRequest, db: Session = Depends(get_db),
                     user: CurrentUser = Depends(require_role(UserRole.CREDIT_OFFICER))):
    """Generate monthly portfolio quality + sector exposure report (Req 22)."""
    from services.reports import PortfolioLoan, build_portfolio_report, export_excel, export_pdf

    loans = [PortfolioLoan(**loan.model_dump()) for loan in body.loans]
    report = build_portfolio_report(loans, period=body.period, capital_base=body.capital_base)
    excel_key = export_excel(report)
    pdf_key = export_pdf(report)
    record_audit(db, user_id=user.id, action="portfolio_report", resource_id=body.period)
    return {"period": report.period, "total_exposure": report.total_exposure,
            "npa_breakdown": report.npa_breakdown, "sector_exposure": report.sector_exposure,
            "exposure_limit_breaches": report.exposure_limit_breaches,
            "excel_key": excel_key, "pdf_key": pdf_key}


@router.get("/healthz", tags=["system"])
def healthz():
    """Liveness probe (Requirement 20.6 support)."""
    return {"status": "ok", "app": settings.app_name, "model_version": settings.model_version}
