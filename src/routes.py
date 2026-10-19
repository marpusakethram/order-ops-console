"""FastAPI route handlers for Order Ops Console."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.attention_rules import (
    evaluate_workspace_attention_flags,
    get_or_create_workspace_settings,
)
from src.auth import AuthContext, resolve_auth_context
from src.config import get_config
from src.csv_parser import (
    SOURCE_TYPES,
    generate_error_report_csv,
    get_all_field_options,
    get_required_fields,
    parse_and_validate_csv,
)
from src.database import get_db
from src.demo_data import (
    ensure_sample_files_on_disk,
    generate_demo_csv_bundles,
    load_demo_dataset,
)
from src.importer import (
    delete_import_batch,
    get_existing_fingerprints,
    ingest_csv_bytes,
    reset_workspace_data,
)
from src.matching import apply_manual_match_decision, reconcile_workspace
from src.models import ImportBatch
from src.queries import (
    execute_nl_query,
    export_orders_csv,
    get_match_review_queue,
    get_order_details,
    get_overview_summary,
    list_orders,
)

router = APIRouter(prefix="/api")


class MatchDecisionRequest(BaseModel):
    raw_record_id: int
    decision_type: str = Field(
        ...,
        description="One of: confirm_match, reject_candidate, leave_unmatched, change_match",
    )
    selected_order_id: int | None = None
    note: str = ""
    decided_by: str = "Operations User"


class AskQuestionRequest(BaseModel):
    question: str


class SettingsUpdateRequest(BaseModel):
    unshipped_threshold_hours: int = Field(ge=6, le=720)
    stale_tracking_threshold_hours: int = Field(ge=12, le=720)
    reference_time_mode: str = Field(default="demo_fixed")
    demo_reference_iso: str | None = None


class PreviewErrorReportRequest(BaseModel):
    filename: str = "import_preview.csv"
    error_rows: list[dict[str, Any]]


async def _read_validated_csv_upload(file: UploadFile) -> tuple[bytes, str]:
    cfg = get_config()
    filename = (file.filename or "upload.csv").strip()
    if not filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .csv files are accepted. Please export your spreadsheet as CSV (UTF-8).",
        )
    max_bytes = cfg.max_upload_size_mb * 1024 * 1024
    raw_bytes = await file.read(max_bytes + 1)
    if len(raw_bytes) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Uploaded file exceeds the {cfg.max_upload_size_mb} MB limit.",
        )
    if not raw_bytes.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The uploaded CSV file is empty.",
        )
    return raw_bytes, filename


@router.get("/health")
def api_health() -> dict[str, Any]:
    cfg = get_config()
    return {
        "status": "ok",
        "app_mode": "connected" if cfg.is_postgres_configured else "demo",
        "database_backend": "Supabase PostgreSQL" if cfg.is_postgres_configured else "Local SQLite",
        "supabase_configured": cfg.is_supabase_configured,
        "gemini_configured": cfg.is_gemini_configured,
        "gemini_model": cfg.gemini_model if cfg.is_gemini_configured else None,
        "require_auth": cfg.require_auth,
        "max_upload_size_mb": cfg.max_upload_size_mb,
    }


@router.get("/overview")
def api_overview(
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(resolve_auth_context),
) -> dict[str, Any]:
    return get_overview_summary(db, auth.workspace_id)


@router.post("/demo/load")
def api_load_demo(
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(resolve_auth_context),
) -> dict[str, Any]:
    ensure_sample_files_on_disk()
    return load_demo_dataset(db, auth.workspace_id)


@router.post("/demo/clear")
def api_clear_workspace(
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(resolve_auth_context),
) -> dict[str, Any]:
    reset_workspace_data(db, auth.workspace_id)
    settings = get_or_create_workspace_settings(db, auth.workspace_id)
    settings.is_demo_dataset = False
    settings.last_imported_at = None
    db.commit()
    return {"status": "cleared", "workspace_id": auth.workspace_id}


@router.get("/samples/{filename}")
def api_download_sample_csv(filename: str) -> Response:
    bundles = generate_demo_csv_bundles()
    if filename not in bundles:
        raise HTTPException(status_code=404, detail=f"Sample file '{filename}' not found.")
    return Response(
        content=bundles[filename],
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import/preview")
async def api_import_preview(
    file: UploadFile = File(...),
    source_type: str = Form(...),
    custom_mapping: str = Form(default=""),
    date_format_preference: str = Form(default="auto"),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(resolve_auth_context),
) -> dict[str, Any]:
    if source_type not in SOURCE_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid source_type '{source_type}'.")

    raw_bytes, filename = await _read_validated_csv_upload(file)

    parsed_mapping: dict[str, str] | None = None
    if custom_mapping and custom_mapping.strip():
        try:
            parsed_mapping = json.loads(custom_mapping)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid custom_mapping JSON: {exc}") from exc

    existing_fps = get_existing_fingerprints(db, auth.workspace_id, source_type)
    res = parse_and_validate_csv(
        raw_bytes=raw_bytes,
        filename=filename,
        source_type=source_type,
        custom_mapping=parsed_mapping,
        date_format_preference=date_format_preference,
        existing_fingerprints=existing_fps,
    )

    return {
        "source_type": res.source_type,
        "filename": res.filename,
        "encoding_used": res.encoding_used,
        "headers": res.headers,
        "field_options": get_all_field_options(source_type),
        "required_fields": get_required_fields(source_type),
        "suggested_mapping": res.column_mapping,
        "missing_required_fields": res.missing_required_fields,
        "date_format_preference": res.date_format_preference,
        "ambiguous_date_samples": res.ambiguous_date_samples,
        "counts": {
            "total_rows": res.total_rows,
            "accepted_rows": res.accepted_rows,
            "rejected_rows": res.rejected_rows,
            "duplicate_rows": res.duplicate_rows,
        },
        "sample_rows": res.sample_rows,
        "error_rows": res.error_rows[:50],
        "can_import": (len(res.missing_required_fields) == 0 and res.accepted_rows > 0 and not (res.ambiguous_date_samples and date_format_preference.lower() == "auto")),
    }


@router.post("/import/commit")
async def api_import_commit(
    file: UploadFile = File(...),
    source_type: str = Form(...),
    custom_mapping: str = Form(default=""),
    date_format_preference: str = Form(default="DD/MM/YYYY"),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(resolve_auth_context),
) -> dict[str, Any]:
    if source_type not in SOURCE_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid source_type '{source_type}'.")

    raw_bytes, filename = await _read_validated_csv_upload(file)
    parsed_mapping: dict[str, str] | None = None
    if custom_mapping and custom_mapping.strip():
        try:
            parsed_mapping = json.loads(custom_mapping)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid custom_mapping JSON: {exc}") from exc

    try:
        summary = ingest_csv_bytes(
            db,
            workspace_id=auth.workspace_id,
            raw_bytes=raw_bytes,
            filename=filename,
            source_type=source_type,
            custom_mapping=parsed_mapping,
            date_format_preference=date_format_preference,
            is_demo=False,
        )
        return summary
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/import/batches/{batch_id}/error-report.csv")
def api_batch_error_report(
    batch_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(resolve_auth_context),
) -> Response:
    batch = db.scalar(
        select(ImportBatch).where(
            ImportBatch.id == batch_id,
            ImportBatch.workspace_id == auth.workspace_id,
        )
    )
    if not batch:
        raise HTTPException(status_code=404, detail="Import batch not found.")
    error_rows = json.loads(batch.error_report_json or "[]")
    csv_text = generate_error_report_csv(error_rows, filename=batch.filename)
    return Response(
        content=csv_text.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="error_report_batch_{batch_id}.csv"'},
    )


@router.post("/import/preview-error-report.csv")
def api_preview_error_report(req: PreviewErrorReportRequest) -> Response:
    csv_text = generate_error_report_csv(req.error_rows, filename=req.filename)
    return Response(
        content=csv_text.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="validation_error_report.csv"'},
    )


@router.delete("/import/batches/{batch_id}")
def api_delete_batch(
    batch_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(resolve_auth_context),
) -> dict[str, Any]:
    try:
        return delete_import_batch(db, auth.workspace_id, batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/reconcile")
def api_reconcile(
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(resolve_auth_context),
) -> dict[str, Any]:
    return reconcile_workspace(db, auth.workspace_id)


@router.get("/orders")
def api_list_orders(
    search: str = Query(default=""),
    date_from: str = Query(default=""),
    date_to: str = Query(default=""),
    courier: str = Query(default=""),
    shipment_status: str = Query(default=""),
    fulfillment_status: str = Query(default=""),
    attention_level: str = Query(default=""),
    rule_code: str = Query(default=""),
    has_open_complaints: str = Query(default=""),
    payment_method: str = Query(default=""),
    city: str = Query(default=""),
    match_status: str = Query(default=""),
    min_stale_days: int | None = Query(default=None),
    min_unshipped_days: int | None = Query(default=None),
    sort_by: str = Query(default="priority"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(resolve_auth_context),
) -> dict[str, Any]:
    return list_orders(
        db,
        auth.workspace_id,
        search=search,
        date_from=date_from,
        date_to=date_to,
        courier=courier,
        shipment_status=shipment_status,
        fulfillment_status=fulfillment_status,
        attention_level=attention_level,
        rule_code=rule_code,
        has_open_complaints=has_open_complaints if has_open_complaints != "" else None,
        payment_method=payment_method,
        city=city,
        match_status=match_status,
        min_stale_days=min_stale_days,
        min_unshipped_days=min_unshipped_days,
        sort_by=sort_by,
        page=page,
        page_size=page_size,
    )


@router.get("/orders/export.csv")
def api_export_orders(
    search: str = Query(default=""),
    date_from: str = Query(default=""),
    date_to: str = Query(default=""),
    courier: str = Query(default=""),
    shipment_status: str = Query(default=""),
    fulfillment_status: str = Query(default=""),
    attention_level: str = Query(default=""),
    rule_code: str = Query(default=""),
    has_open_complaints: str = Query(default=""),
    payment_method: str = Query(default=""),
    city: str = Query(default=""),
    match_status: str = Query(default=""),
    min_stale_days: int | None = Query(default=None),
    min_unshipped_days: int | None = Query(default=None),
    sort_by: str = Query(default="priority"),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(resolve_auth_context),
) -> Response:
    csv_text = export_orders_csv(
        db,
        auth.workspace_id,
        search=search,
        date_from=date_from,
        date_to=date_to,
        courier=courier,
        shipment_status=shipment_status,
        fulfillment_status=fulfillment_status,
        attention_level=attention_level,
        rule_code=rule_code,
        has_open_complaints=has_open_complaints if has_open_complaints != "" else None,
        payment_method=payment_method,
        city=city,
        match_status=match_status,
        min_stale_days=min_stale_days,
        min_unshipped_days=min_unshipped_days,
        sort_by=sort_by,
    )
    return Response(
        content=csv_text.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="order_ops_filtered_export.csv"'},
    )


@router.get("/orders/{order_id}")
def api_get_order_details(
    order_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(resolve_auth_context),
) -> dict[str, Any]:
    try:
        return get_order_details(db, auth.workspace_id, order_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/match-review")
def api_get_match_review(
    status_filter: str = Query(default="actionable"),
    source_filter: str = Query(default="all"),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(resolve_auth_context),
) -> dict[str, Any]:
    return get_match_review_queue(
        db,
        auth.workspace_id,
        status_filter=status_filter,
        source_filter=source_filter,
    )


@router.post("/match-review/decide")
def api_post_match_decision(
    req: MatchDecisionRequest,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(resolve_auth_context),
) -> dict[str, Any]:
    try:
        return apply_manual_match_decision(
            db,
            workspace_id=auth.workspace_id,
            raw_record_id=req.raw_record_id,
            decision_type=req.decision_type,
            selected_order_id=req.selected_order_id,
            note=req.note,
            decided_by=req.decided_by or auth.user_label,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ask")
def api_ask_orders(
    req: AskQuestionRequest,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(resolve_auth_context),
) -> dict[str, Any]:
    return execute_nl_query(db, auth.workspace_id, req.question)


@router.get("/settings")
def api_get_settings(
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(resolve_auth_context),
) -> dict[str, Any]:
    s = get_or_create_workspace_settings(db, auth.workspace_id)
    db.commit()
    return {
        "workspace_id": s.workspace_id,
        "unshipped_threshold_hours": s.unshipped_threshold_hours,
        "stale_tracking_threshold_hours": s.stale_tracking_threshold_hours,
        "reference_time_mode": s.reference_time_mode,
        "demo_reference_iso": s.demo_reference_iso,
        "is_demo_dataset": s.is_demo_dataset,
    }


@router.put("/settings")
def api_update_settings(
    req: SettingsUpdateRequest,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(resolve_auth_context),
) -> dict[str, Any]:
    s = get_or_create_workspace_settings(db, auth.workspace_id)
    s.unshipped_threshold_hours = req.unshipped_threshold_hours
    s.stale_tracking_threshold_hours = req.stale_tracking_threshold_hours
    if req.reference_time_mode in ("demo_fixed", "live_ist"):
        s.reference_time_mode = req.reference_time_mode
    if req.demo_reference_iso:
        s.demo_reference_iso = req.demo_reference_iso
    db.flush()
    flags_summary = evaluate_workspace_attention_flags(db, auth.workspace_id)
    db.commit()
    return {
        "status": "updated",
        "unshipped_threshold_hours": s.unshipped_threshold_hours,
        "stale_tracking_threshold_hours": s.stale_tracking_threshold_hours,
        "reference_time_mode": s.reference_time_mode,
        "flags_summary": flags_summary,
    }
