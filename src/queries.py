"""Read queries, operational summaries, order details timeline, match review queue, and safe CSV export."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.attention_rules import (
    ACTIVE_MOVING_STATUSES,
    LEVEL_HIGH,
    LEVEL_LOW,
    LEVEL_MEDIUM,
    LEVEL_NONE,
    get_or_create_workspace_settings,
    get_workspace_reference_utc,
)
from src.config import IST, get_config
from src.matching import MATCH_REVIEW, MATCH_UNMATCHED
from src.models import (
    AttentionFlag,
    Complaint,
    ImportBatch,
    MatchDecision,
    Order,
    OrderLineItem,
    RawSourceRecord,
    Shipment,
    ShipmentEvent,
)
from src.nl_query import StructuredOrderQuery, translate_natural_language_question
from src.normalization import (
    format_ist,
    format_paise_to_inr,
    format_shipment_status_label,
    sanitize_csv_cell,
    to_ist_iso,
)

_PRIORITY_SORT = {
    LEVEL_HIGH: 3,
    LEVEL_MEDIUM: 2,
    LEVEL_LOW: 1,
    LEVEL_NONE: 0,
}


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _assemble_workspace_bundle(db: Session, workspace_id: str) -> dict[str, Any]:
    """Load orders and their associated shipments, complaints, and flags in bulk for fast filtering."""
    settings = get_or_create_workspace_settings(db, workspace_id)
    ref_utc = get_workspace_reference_utc(settings)

    orders = list(db.scalars(select(Order).where(Order.workspace_id == workspace_id)).all())
    shipments = list(db.scalars(select(Shipment).where(Shipment.workspace_id == workspace_id)).all())
    complaints = list(db.scalars(select(Complaint).where(Complaint.workspace_id == workspace_id)).all())
    flags = list(db.scalars(select(AttentionFlag).where(AttentionFlag.workspace_id == workspace_id)).all())

    shipments_by_order: dict[int, list[Shipment]] = {}
    for s in shipments:
        if s.order_id:
            shipments_by_order.setdefault(s.order_id, []).append(s)

    complaints_by_order: dict[int, list[Complaint]] = {}
    for c in complaints:
        if c.order_id:
            complaints_by_order.setdefault(c.order_id, []).append(c)

    flags_by_order: dict[int, list[AttentionFlag]] = {}
    for f in flags:
        flags_by_order.setdefault(f.order_id, []).append(f)

    return {
        "settings": settings,
        "ref_utc": ref_utc,
        "orders": orders,
        "shipments": shipments,
        "complaints": complaints,
        "flags": flags,
        "shipments_by_order": shipments_by_order,
        "complaints_by_order": complaints_by_order,
        "flags_by_order": flags_by_order,
    }


def _build_order_summary_dict(
    ord_obj: Order,
    ord_shipments: list[Shipment],
    ord_complaints: list[Complaint],
    ord_flags: list[AttentionFlag],
    ref_utc: datetime,
) -> dict[str, Any]:
    # Sort shipments so the most recently updated or highest-risk shipment is primary
    sorted_shipments = sorted(
        ord_shipments,
        key=lambda s: (_ensure_utc(s.latest_event_at) or datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True,
    )

    if sorted_shipments:
        primary_shp = sorted_shipments[0]
        couriers_unique = list(dict.fromkeys(s.courier_name for s in sorted_shipments if s.courier_name))
        courier_display = ", ".join(couriers_unique)
        if len(sorted_shipments) > 1:
            tracking_display = f"{primary_shp.tracking_number_raw} (+{len(sorted_shipments) - 1} more)"
        else:
            tracking_display = primary_shp.tracking_number_raw
        latest_status_norm = primary_shp.current_status_norm
        latest_status_raw = primary_shp.current_status_raw
        latest_status_label = format_shipment_status_label(latest_status_norm)
        last_update_dt = primary_shp.latest_event_at
    else:
        courier_display = "—"
        tracking_display = "—"
        if ord_obj.is_cancelled or ord_obj.fulfillment_status == "cancelled":
            latest_status_norm = "cancelled"
            latest_status_raw = "Order Cancelled"
            latest_status_label = "Cancelled"
        else:
            latest_status_norm = "awaiting_shipment"
            latest_status_raw = "Awaiting Dispatch"
            latest_status_label = "Awaiting Shipment"
        last_update_dt = None

    open_complaints = [c for c in ord_complaints if c.status.lower() != "resolved"]
    rule_codes = [f.rule_code for f in ord_flags]
    rule_names = [f.rule_name for f in ord_flags]

    # Compute max stale hours across active moving shipments
    max_stale_hours = 0.0
    for s in sorted_shipments:
        if s.current_status_norm in ACTIVE_MOVING_STATUSES and s.latest_event_at:
            ev_utc = _ensure_utc(s.latest_event_at)
            if ev_utc and ref_utc > ev_utc:
                h = (ref_utc - ev_utc).total_seconds() / 3600.0
                if h > max_stale_hours:
                    max_stale_hours = h

    unshipped_hours = 0.0
    if not ord_obj.is_cancelled and ord_obj.fulfillment_status == "unfulfilled" and ord_obj.order_date:
        o_utc = _ensure_utc(ord_obj.order_date)
        if o_utc and ref_utc > o_utc:
            unshipped_hours = (ref_utc - o_utc).total_seconds() / 3600.0

    return {
        "id": ord_obj.id,
        "order_number": ord_obj.order_number_raw,
        "order_number_norm": ord_obj.order_number_norm,
        "order_date_utc": _ensure_utc(ord_obj.order_date).isoformat() if ord_obj.order_date else None,
        "order_date_ist": format_ist(ord_obj.order_date),
        "order_date_short": format_ist(ord_obj.order_date, include_time=False),
        "customer_name": ord_obj.customer_name or "—",
        "customer_phone_masked": ord_obj.customer_phone_masked or "—",
        "city": ord_obj.city or "—",
        "state": ord_obj.state or "",
        "total_amount_paise": ord_obj.total_amount_paise,
        "order_value_inr": format_paise_to_inr(ord_obj.total_amount_paise),
        "payment_method": ord_obj.payment_method,
        "financial_status": ord_obj.financial_status,
        "fulfillment_status": ord_obj.fulfillment_status,
        "is_cancelled": ord_obj.is_cancelled,
        "shipment_count": len(sorted_shipments),
        "courier": courier_display,
        "couriers_list": [s.courier_name for s in sorted_shipments],
        "tracking_number": tracking_display,
        "tracking_numbers_list": [s.tracking_number_raw for s in sorted_shipments],
        "latest_shipment_status": latest_status_norm,
        "latest_shipment_status_raw": latest_status_raw,
        "latest_shipment_status_label": latest_status_label,
        "all_shipment_statuses": [s.current_status_norm for s in sorted_shipments],
        "last_tracking_update_utc": _ensure_utc(last_update_dt).isoformat() if last_update_dt else None,
        "last_tracking_update_ist": format_ist(last_update_dt) if last_update_dt else "No scans yet",
        "complaint_count": len(ord_complaints),
        "open_complaint_count": len(open_complaints),
        "attention_level": ord_obj.attention_level,
        "reason_for_attention": ord_obj.primary_attention_reason or "No active flags",
        "rule_codes": rule_codes,
        "rule_names": rule_names,
        "match_status": ord_obj.match_status,
        "max_stale_hours": round(max_stale_hours, 1),
        "unshipped_hours": round(unshipped_hours, 1),
    }


def get_overview_summary(db: Session, workspace_id: str) -> dict[str, Any]:
    """Build the operational summary cards and system status for the Overview page."""
    bundle = _assemble_workspace_bundle(db, workspace_id)
    settings = bundle["settings"]
    ref_utc = bundle["ref_utc"]
    orders = bundle["orders"]

    raw_records = list(
        db.scalars(
            select(RawSourceRecord).where(
                RawSourceRecord.workspace_id == workspace_id,
                RawSourceRecord.validation_status == "accepted",
                RawSourceRecord.source_type != "shopify_orders",
            )
        ).all()
    )
    batches = list(
        db.scalars(
            select(ImportBatch)
            .where(ImportBatch.workspace_id == workspace_id)
            .order_by(ImportBatch.imported_at.desc())
        ).all()
    )

    order_rows = [
        _build_order_summary_dict(
            o,
            bundle["shipments_by_order"].get(o.id, []),
            bundle["complaints_by_order"].get(o.id, []),
            bundle["flags_by_order"].get(o.id, []),
            ref_utc,
        )
        for o in orders
    ]

    total_orders = len(order_rows)
    awaiting_shipment = sum(
        1
        for r in order_rows
        if not r["is_cancelled"]
        and (r["fulfillment_status"] == "unfulfilled" or r["latest_shipment_status"] == "awaiting_shipment")
    )
    in_transit = sum(
        1
        for r in order_rows
        if any(st in ("awaiting_pickup", "in_transit", "out_for_delivery") for st in r["all_shipment_statuses"])
    )
    stale_tracking = sum(1 for r in order_rows if "STALE_TRACKING" in r["rule_codes"])
    failed_attempts = sum(
        1
        for r in order_rows
        if "FAILED_DELIVERY_ATTEMPT" in r["rule_codes"]
        or "failed_attempt" in r["all_shipment_statuses"]
    )
    returning_to_origin = sum(
        1
        for r in order_rows
        if "RTO_INITIATED" in r["rule_codes"]
        or any(st in ("rto_initiated", "rto_delivered") for st in r["all_shipment_statuses"])
    )
    orders_with_open_complaints = sum(1 for r in order_rows if r["open_complaint_count"] > 0)
    total_open_complaints = sum(
        1 for c in bundle["complaints"] if c.status.lower() != "resolved"
    )
    orders_needing_review = sum(1 for r in order_rows if r["attention_level"] != LEVEL_NONE)
    high_priority_orders = sum(1 for r in order_rows if r["attention_level"] == LEVEL_HIGH)
    medium_priority_orders = sum(1 for r in order_rows if r["attention_level"] == LEVEL_MEDIUM)
    low_priority_orders = sum(1 for r in order_rows if r["attention_level"] == LEVEL_LOW)

    unmatched_or_ambiguous_records = sum(
        1 for rec in raw_records if rec.reconciliation_status in (MATCH_REVIEW, MATCH_UNMATCHED)
    )
    ambiguous_records = sum(1 for rec in raw_records if rec.reconciliation_status == MATCH_REVIEW)
    strictly_unmatched_records = sum(1 for rec in raw_records if rec.reconciliation_status == MATCH_UNMATCHED)

    couriers_set = sorted(
        {c for r in order_rows for c in r["couriers_list"] if c and c != "—"}
    )
    cities_set = sorted({r["city"] for r in order_rows if r["city"] and r["city"] != "—"})

    cfg = get_config()
    return {
        "workspace_id": workspace_id,
        "is_demo_dataset": bool(settings.is_demo_dataset),
        "app_mode": "connected" if cfg.is_postgres_configured else "demo",
        "database_backend": "Supabase PostgreSQL" if cfg.is_postgres_configured else "Local SQLite (Demo Mode)",
        "gemini_configured": cfg.is_gemini_configured,
        "gemini_model": cfg.gemini_model if cfg.is_gemini_configured else "Deterministic Parser (No API Key Needed)",
        "last_imported_at_ist": format_ist(settings.last_imported_at) if settings.last_imported_at else "No data imported yet",
        "reference_time_mode": settings.reference_time_mode,
        "reference_time_ist": format_ist(ref_utc),
        "reference_time_iso": to_ist_iso(ref_utc),
        "thresholds": {
            "unshipped_threshold_hours": settings.unshipped_threshold_hours,
            "unshipped_threshold_days": round(settings.unshipped_threshold_hours / 24, 1),
            "stale_tracking_threshold_hours": settings.stale_tracking_threshold_hours,
            "stale_tracking_threshold_days": round(settings.stale_tracking_threshold_hours / 24, 1),
        },
        "cards": {
            "total_orders": total_orders,
            "orders_awaiting_shipment": awaiting_shipment,
            "orders_in_transit": in_transit,
            "orders_with_stale_tracking": stale_tracking,
            "orders_with_failed_delivery_attempts": failed_attempts,
            "orders_returning_to_origin": returning_to_origin,
            "open_customer_complaints": orders_with_open_complaints,
            "total_open_complaint_tickets": total_open_complaints,
            "orders_needing_review": orders_needing_review,
            "high_priority_orders": high_priority_orders,
            "medium_priority_orders": medium_priority_orders,
            "low_priority_orders": low_priority_orders,
            "unmatched_source_records": unmatched_or_ambiguous_records,
            "ambiguous_source_records": ambiguous_records,
            "strictly_unmatched_records": strictly_unmatched_records,
        },
        "filter_options": {
            "couriers": couriers_set,
            "cities": cities_set,
        },
        "recent_batches": [
            {
                "id": b.id,
                "source_type": b.source_type,
                "filename": b.filename,
                "is_demo": b.is_demo,
                "total_rows": b.total_rows,
                "accepted_rows": b.accepted_rows,
                "rejected_rows": b.rejected_rows,
                "duplicate_rows": b.duplicate_rows,
                "imported_at_ist": format_ist(b.imported_at),
            }
            for b in batches[:10]
        ],
    }


def filter_order_rows(
    order_rows: list[dict[str, Any]],
    *,
    search: str = "",
    date_from: str = "",
    date_to: str = "",
    courier: str = "",
    shipment_status: str = "",
    fulfillment_status: str = "",
    attention_level: str = "",
    rule_code: str = "",
    has_open_complaints: str | bool | None = None,
    payment_method: str = "",
    city: str = "",
    match_status: str = "",
    min_stale_days: int | None = None,
    min_unshipped_days: int | None = None,
    sort_by: str = "priority",
) -> list[dict[str, Any]]:
    """Apply search, filters, and sorting to assembled order summary rows."""
    filtered = order_rows

    if search and search.strip():
        q = search.strip().lower()
        filtered = [
            r
            for r in filtered
            if q in r["order_number"].lower()
            or q in r["order_number_norm"].lower()
            or q in r["customer_name"].lower()
            or any(q in tn.lower() for tn in r["tracking_numbers_list"])
        ]

    if date_from and date_from.strip():
        try:
            df = datetime.fromisoformat(date_from.strip()).replace(tzinfo=IST).astimezone(timezone.utc)
            filtered = [
                r
                for r in filtered
                if r["order_date_utc"] and datetime.fromisoformat(r["order_date_utc"]) >= df
            ]
        except ValueError:
            pass

    if date_to and date_to.strip():
        try:
            dt_end = (
                datetime.fromisoformat(date_to.strip())
                .replace(hour=23, minute=59, second=59, tzinfo=IST)
                .astimezone(timezone.utc)
            )
            filtered = [
                r
                for r in filtered
                if r["order_date_utc"] and datetime.fromisoformat(r["order_date_utc"]) <= dt_end
            ]
        except ValueError:
            pass

    if courier and courier.strip() and courier.lower() != "all":
        c_low = courier.strip().lower()
        filtered = [
            r for r in filtered if any(c_low == c.lower() for c in r["couriers_list"])
        ]

    if fulfillment_status and fulfillment_status.strip() and fulfillment_status.lower() not in ("all", "any"):
        ff_low = fulfillment_status.strip().lower()
        if ff_low == "unfulfilled":
            filtered = [
                r for r in filtered if not r["is_cancelled"] and r["fulfillment_status"] == "unfulfilled"
            ]
        else:
            filtered = [r for r in filtered if r["fulfillment_status"].lower() == ff_low]

    if shipment_status and shipment_status.strip() and shipment_status.lower() not in ("all", "any"):
        st_low = shipment_status.strip().lower()
        if st_low == "awaiting_shipment":
            filtered = [
                r
                for r in filtered
                if not r["is_cancelled"]
                and (r["fulfillment_status"] == "unfulfilled" or r["latest_shipment_status"] == "awaiting_shipment")
            ]
        elif st_low == "in_transit":
            filtered = [
                r
                for r in filtered
                if any(s in ("awaiting_pickup", "in_transit", "out_for_delivery") for s in r["all_shipment_statuses"])
            ]
        elif st_low == "rto":
            filtered = [
                r
                for r in filtered
                if any(s in ("rto_initiated", "rto_delivered") for s in r["all_shipment_statuses"])
                or "RTO_INITIATED" in r["rule_codes"]
            ]
        elif st_low == "failed_attempt":
            filtered = [
                r
                for r in filtered
                if "failed_attempt" in r["all_shipment_statuses"]
                or "FAILED_DELIVERY_ATTEMPT" in r["rule_codes"]
            ]
        else:
            filtered = [
                r
                for r in filtered
                if r["latest_shipment_status"] == st_low or st_low in r["all_shipment_statuses"]
            ]

    if attention_level and attention_level.strip() and attention_level.lower() not in ("all", "any"):
        al = attention_level.strip()
        if al == "flagged_any":
            filtered = [r for r in filtered if r["attention_level"] != LEVEL_NONE]
        else:
            filtered = [r for r in filtered if r["attention_level"].lower() == al.lower()]

    if rule_code and rule_code.strip() and rule_code.lower() not in ("all", "any"):
        rc = rule_code.strip().upper()
        filtered = [r for r in filtered if rc in r["rule_codes"]]

    if min_stale_days is not None and min_stale_days > 0:
        min_hours = min_stale_days * 24.0
        filtered = [r for r in filtered if r["max_stale_hours"] >= min_hours]

    if min_unshipped_days is not None and min_unshipped_days > 0:
        min_u_hours = min_unshipped_days * 24.0
        filtered = [r for r in filtered if r["unshipped_hours"] >= min_u_hours]

    if has_open_complaints not in (None, "", "all", "any"):
        want_open = str(has_open_complaints).lower() in ("true", "1", "yes")
        if want_open:
            filtered = [r for r in filtered if r["open_complaint_count"] > 0]
        else:
            filtered = [r for r in filtered if r["open_complaint_count"] == 0]

    if payment_method and payment_method.strip() and payment_method.lower() not in ("all", "any"):
        pm = payment_method.strip().upper()
        filtered = [r for r in filtered if r["payment_method"].upper() == pm]

    if city and city.strip() and city.lower() not in ("all", "any"):
        city_low = city.strip().lower()
        filtered = [r for r in filtered if r["city"].lower() == city_low]

    if match_status and match_status.strip() and match_status.lower() not in ("all", "any"):
        ms_low = match_status.strip().lower()
        filtered = [r for r in filtered if r["match_status"].lower() == ms_low]

    # Sorting
    if sort_by == "order_date_asc":
        filtered.sort(key=lambda r: r["order_date_utc"] or "")
    elif sort_by == "order_date_desc":
        filtered.sort(key=lambda r: r["order_date_utc"] or "", reverse=True)
    elif sort_by == "amount_desc":
        filtered.sort(key=lambda r: r["total_amount_paise"], reverse=True)
    elif sort_by == "amount_asc":
        filtered.sort(key=lambda r: r["total_amount_paise"])
    else:
        # Default: operational priority first (High -> Medium -> Low -> None), then complaint count, then order date
        filtered.sort(
            key=lambda r: (
                _PRIORITY_SORT.get(r["attention_level"], 0),
                r["open_complaint_count"],
                len(r["rule_codes"]),
                r["order_date_utc"] or "",
            ),
            reverse=True,
        )

    return filtered


def list_orders(
    db: Session,
    workspace_id: str,
    *,
    search: str = "",
    date_from: str = "",
    date_to: str = "",
    courier: str = "",
    shipment_status: str = "",
    fulfillment_status: str = "",
    attention_level: str = "",
    rule_code: str = "",
    has_open_complaints: str | bool | None = None,
    payment_method: str = "",
    city: str = "",
    match_status: str = "",
    min_stale_days: int | None = None,
    min_unshipped_days: int | None = None,
    sort_by: str = "priority",
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    """Return a filtered, sorted, paginated list of orders."""
    bundle = _assemble_workspace_bundle(db, workspace_id)
    ref_utc = bundle["ref_utc"]

    all_rows = [
        _build_order_summary_dict(
            o,
            bundle["shipments_by_order"].get(o.id, []),
            bundle["complaints_by_order"].get(o.id, []),
            bundle["flags_by_order"].get(o.id, []),
            ref_utc,
        )
        for o in bundle["orders"]
    ]

    filtered = filter_order_rows(
        all_rows,
        search=search,
        date_from=date_from,
        date_to=date_to,
        courier=courier,
        shipment_status=shipment_status,
        fulfillment_status=fulfillment_status,
        attention_level=attention_level,
        rule_code=rule_code,
        has_open_complaints=has_open_complaints,
        payment_method=payment_method,
        city=city,
        match_status=match_status,
        min_stale_days=min_stale_days,
        min_unshipped_days=min_unshipped_days,
        sort_by=sort_by,
    )

    total_matching = len(filtered)
    page = max(1, page)
    page_size = max(1, min(200, page_size))
    total_pages = max(1, (total_matching + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages

    start = (page - 1) * page_size
    items = filtered[start : start + page_size]

    return {
        "total": total_matching,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "reference_time_ist": format_ist(ref_utc),
        "items": items,
    }


def get_order_details(db: Session, workspace_id: str, order_id: int) -> dict[str, Any]:
    """Return complete details, source provenance, shipments, complaints, timeline, and flags for an Order."""
    ord_obj = db.scalar(
        select(Order).where(Order.id == order_id, Order.workspace_id == workspace_id)
    )
    if not ord_obj:
        raise ValueError(f"Order #{order_id} not found.")

    settings = get_or_create_workspace_settings(db, workspace_id)
    ref_utc = get_workspace_reference_utc(settings)

    line_items = list(
        db.scalars(
            select(OrderLineItem)
            .where(OrderLineItem.order_id == ord_obj.id)
            .order_by(OrderLineItem.id.asc())
        ).all()
    )
    shipments = list(
        db.scalars(
            select(Shipment)
            .where(Shipment.order_id == ord_obj.id)
            .order_by(Shipment.id.asc())
        ).all()
    )
    complaints = list(
        db.scalars(
            select(Complaint)
            .where(Complaint.order_id == ord_obj.id)
            .order_by(Complaint.complaint_at.asc())
        ).all()
    )
    flags = list(
        db.scalars(
            select(AttentionFlag)
            .where(AttentionFlag.order_id == ord_obj.id)
            .order_by(AttentionFlag.id.asc())
        ).all()
    )

    # Also find any ambiguous / candidate raw records that reference this order!
    candidate_raw_records: list[dict[str, Any]] = []
    review_raws = list(
        db.scalars(
            select(RawSourceRecord).where(
                RawSourceRecord.workspace_id == workspace_id,
                RawSourceRecord.reconciliation_status == MATCH_REVIEW,
            )
        ).all()
    )
    for rr in review_raws:
        try:
            cands = json.loads(rr.candidate_matches_json or "[]")
            for c in cands:
                if c.get("order_id") == ord_obj.id:
                    candidate_raw_records.append(
                        {
                            "raw_record_id": rr.id,
                            "source_type": rr.source_type,
                            "source_filename": rr.source_filename,
                            "row_number": rr.row_number,
                            "explanation": rr.match_explanation,
                            "heuristic_score": c.get("heuristic_score", rr.match_score),
                            "reasons": c.get("reasons", []),
                        }
                    )
        except Exception:
            pass

    # Build chronological timeline across Order creation, Shipment scans, and Complaints
    timeline: list[dict[str, Any]] = []
    if ord_obj.order_date:
        timeline.append(
            {
                "timestamp_utc": _ensure_utc(ord_obj.order_date).isoformat(),
                "timestamp_ist": format_ist(ord_obj.order_date),
                "event_type": "order_created",
                "badge": "Shopify Order",
                "title": f"Order {ord_obj.order_number_raw} Placed ({ord_obj.payment_method})",
                "description": (
                    f"{len(line_items)} line item(s) totaling {format_paise_to_inr(ord_obj.total_amount_paise)} "
                    f"for {ord_obj.customer_name} ({ord_obj.city})."
                ),
                "source_reference": f"{ord_obj.source_filename} (Row {ord_obj.source_row_number})",
            }
        )

    shipments_payload: list[dict[str, Any]] = []
    for shp in shipments:
        events = list(
            db.scalars(
                select(ShipmentEvent)
                .where(ShipmentEvent.shipment_id == shp.id)
                .order_by(ShipmentEvent.event_at.asc(), ShipmentEvent.id.asc())
            ).all()
        )
        ev_list = []
        for ev in events:
            ev_list.append(
                {
                    "id": ev.id,
                    "status_raw": ev.status_raw,
                    "status_norm": ev.status_norm,
                    "status_label": format_shipment_status_label(ev.status_norm),
                    "event_at_ist": format_ist(ev.event_at),
                    "location": ev.location or "—",
                    "remarks": ev.remarks or "",
                    "delivery_attempts": ev.delivery_attempts,
                    "source_filename": ev.source_filename,
                    "source_row_number": ev.source_row_number,
                }
            )
            timeline.append(
                {
                    "timestamp_utc": _ensure_utc(ev.event_at).isoformat(),
                    "timestamp_ist": format_ist(ev.event_at),
                    "event_type": "shipment_scan",
                    "badge": f"{shp.courier_name} ({shp.tracking_number_raw})",
                    "title": f"{ev.status_raw} — {ev.location or 'Hub'}",
                    "description": ev.remarks or f"Normalized status: {format_shipment_status_label(ev.status_norm)}",
                    "source_reference": f"{ev.source_filename} (Row {ev.source_row_number})",
                }
            )

        shipments_payload.append(
            {
                "id": shp.id,
                "tracking_number": shp.tracking_number_raw,
                "courier_name": shp.courier_name,
                "current_status_raw": shp.current_status_raw,
                "current_status_norm": shp.current_status_norm,
                "current_status_label": format_shipment_status_label(shp.current_status_norm),
                "latest_event_at_ist": format_ist(shp.latest_event_at),
                "latest_location": shp.latest_location or "—",
                "expected_delivery_ist": format_ist(shp.expected_delivery_date, include_time=False)
                if shp.expected_delivery_date
                else "—",
                "failed_attempts_count": shp.failed_attempts_count,
                "has_conflicting_status": shp.has_conflicting_status,
                "conflict_explanation": shp.conflict_explanation,
                "match_status": shp.match_status,
                "match_method": shp.match_method,
                "match_score": shp.match_score,
                "match_explanation": shp.match_explanation,
                "source_filename": shp.source_filename,
                "source_row_number": shp.source_row_number,
                "raw_record_id": shp.raw_record_id,
                "events": ev_list,
            }
        )

    complaints_payload: list[dict[str, Any]] = []
    for comp in complaints:
        complaints_payload.append(
            {
                "id": comp.id,
                "complaint_ref": comp.complaint_ref,
                "complaint_at_ist": format_ist(comp.complaint_at),
                "customer_name": comp.customer_name or "—",
                "customer_phone_masked": comp.customer_phone_masked,
                "channel": comp.channel,
                "issue_category": comp.issue_category,
                "message_text": comp.message_text,
                "status": comp.status,
                "claims_not_received": comp.claims_not_received,
                "match_status": comp.match_status,
                "match_method": comp.match_method,
                "match_score": comp.match_score,
                "match_explanation": comp.match_explanation,
                "source_filename": comp.source_filename,
                "source_row_number": comp.source_row_number,
                "raw_record_id": comp.raw_record_id,
            }
        )
        timeline.append(
            {
                "timestamp_utc": _ensure_utc(comp.complaint_at).isoformat(),
                "timestamp_ist": format_ist(comp.complaint_at),
                "event_type": "customer_complaint",
                "badge": f"WhatsApp CSV ({comp.complaint_ref})",
                "title": f"Customer Complaint: {comp.issue_category} [{comp.status}]",
                "description": f"\"{comp.message_text}\"",
                "source_reference": f"{comp.source_filename} (Row {comp.source_row_number})",
            }
        )

    timeline.sort(key=lambda item: item["timestamp_utc"])

    flags_payload: list[dict[str, Any]] = []
    suggested_actions: list[str] = []
    for f in flags:
        refs = json.loads(f.source_references_json or "[]")
        flags_payload.append(
            {
                "id": f.id,
                "rule_code": f.rule_code,
                "rule_name": f.rule_name,
                "attention_level": f.attention_level,
                "triggering_evidence": f.triggering_evidence,
                "threshold_used": f.threshold_used,
                "explanation": f.explanation,
                "suggested_action": f.suggested_action,
                "source_references": refs,
            }
        )
        if f.suggested_action and f.suggested_action not in suggested_actions:
            suggested_actions.append(f.suggested_action)

    if candidate_raw_records:
        suggested_actions.append(
            f"Review {len(candidate_raw_records)} ambiguous source record(s) in Match Review that list this order as a candidate."
        )

    summary_dict = _build_order_summary_dict(ord_obj, shipments, complaints, flags, ref_utc)

    return {
        "order": {
            **summary_dict,
            "customer_email": ord_obj.customer_email or "—",
            "pincode": ord_obj.pincode or "—",
            "promised_delivery_ist": format_ist(ord_obj.promised_delivery_date, include_time=False)
            if ord_obj.promised_delivery_date
            else "—",
            "source_filename": ord_obj.source_filename,
            "source_row_number": ord_obj.source_row_number,
            "match_explanation": ord_obj.match_explanation,
        },
        "line_items": [
            {
                "id": li.id,
                "sku": li.sku or "—",
                "product_title": li.product_title,
                "quantity": li.quantity,
                "unit_price_inr": format_paise_to_inr(li.unit_price_paise),
                "fulfillment_status": li.fulfillment_status,
                "tracking_number": li.tracking_number_raw or "—",
                "courier_name": li.courier_name or "—",
                "source_filename": li.source_filename,
                "source_row_number": li.source_row_number,
            }
            for li in line_items
        ],
        "shipments": shipments_payload,
        "complaints": complaints_payload,
        "attention_flags": flags_payload,
        "suggested_actions": suggested_actions,
        "candidate_review_records": candidate_raw_records,
        "timeline": timeline,
        "reference_time_ist": format_ist(ref_utc),
    }


def get_match_review_queue(
    db: Session,
    workspace_id: str,
    status_filter: str = "actionable",
    source_filter: str = "all",
) -> dict[str, Any]:
    """Return unmatched, ambiguous ('Needs review'), and manually confirmed records plus audit log."""
    raw_records = list(
        db.scalars(
            select(RawSourceRecord)
            .where(
                RawSourceRecord.workspace_id == workspace_id,
                RawSourceRecord.validation_status == "accepted",
                RawSourceRecord.source_type != "shopify_orders",
            )
            .order_by(RawSourceRecord.id.asc())
        ).all()
    )
    orders_by_id: dict[int, Order] = {
        o.id: o for o in db.scalars(select(Order).where(Order.workspace_id == workspace_id)).all()
    }

    items: list[dict[str, Any]] = []
    counts = {
        "needs_review": 0,
        "unmatched": 0,
        "manually_confirmed": 0,
        "auto_matched": 0,
    }

    # Deduplicate courier raw records by tracking_number_norm so multiple scans for the same AWB
    # don't clutter the review queue twice
    seen_courier_awbs: set[str] = set()

    for r in raw_records:
        norm = json.loads(r.normalized_payload_json or "{}")
        if r.source_type == "courier_tracking":
            awb = norm.get("tracking_number_norm", "")
            if awb and awb in seen_courier_awbs:
                continue
            if awb:
                seen_courier_awbs.add(awb)

        st = r.reconciliation_status
        if st == "Needs review":
            counts["needs_review"] += 1
        elif st == "Unmatched":
            counts["unmatched"] += 1
        elif st == "Manually confirmed":
            counts["manually_confirmed"] += 1
        else:
            counts["auto_matched"] += 1

        if source_filter not in ("all", "") and r.source_type != source_filter:
            continue

        if status_filter == "actionable" and st not in ("Needs review", "Unmatched", "Manually confirmed"):
            continue
        elif status_filter == "needs_review" and st != "Needs review":
            continue
        elif status_filter == "unmatched" and st != "Unmatched":
            continue
        elif status_filter == "manually_confirmed" and st != "Manually confirmed":
            continue

        raw_payload = json.loads(r.raw_payload_json or "{}")
        candidates = json.loads(r.candidate_matches_json or "[]")
        matched_ord = orders_by_id.get(r.matched_order_id) if r.matched_order_id else None

        # Mask phone in raw display summary while preserving source context
        if r.source_type == "courier_tracking":
            record_title = f"Courier AWB: {norm.get('tracking_number_raw') or '—'} ({norm.get('courier_name') or 'Courier'})"
            record_subtitle = (
                f"Status: {norm.get('status_raw') or '—'} | "
                f"Order Ref: {norm.get('order_number_ref_raw') or 'None'} | "
                f"Customer: {norm.get('customer_name') or '—'}"
            )
        else:
            record_title = f"WhatsApp Ticket: {norm.get('complaint_ref') or '—'} ({norm.get('issue_category') or 'Inquiry'})"
            record_subtitle = f"From: {norm.get('customer_name') or '—'} ({norm.get('customer_phone_masked') or '—'}) — \"{norm.get('message_text') or ''}\""

        items.append(
            {
                "raw_record_id": r.id,
                "source_type": r.source_type,
                "source_filename": r.source_filename,
                "row_number": r.row_number,
                "record_title": record_title,
                "record_subtitle": record_subtitle,
                "reconciliation_status": r.reconciliation_status,
                "match_method": r.match_method,
                "heuristic_score": r.match_score,
                "heuristic_note": "Heuristic rule score (0-100), not a statistical probability.",
                "match_explanation": r.match_explanation,
                "matched_order": {
                    "id": matched_ord.id,
                    "order_number": matched_ord.order_number_raw,
                    "customer_name": matched_ord.customer_name,
                    "city": matched_ord.city,
                    "order_value_inr": format_paise_to_inr(matched_ord.total_amount_paise),
                }
                if matched_ord
                else None,
                "candidates": candidates,
                "raw_payload": raw_payload,
                "normalized_summary": norm,
            }
        )

    # Sort so "Needs review" comes first, then "Unmatched", then "Manually confirmed"
    status_order = {"Needs review": 0, "Unmatched": 1, "Manually confirmed": 2}
    items.sort(key=lambda x: (status_order.get(x["reconciliation_status"], 9), x["raw_record_id"]))

    # Load audit history of manual decisions
    decisions = list(
        db.scalars(
            select(MatchDecision)
            .where(MatchDecision.workspace_id == workspace_id)
            .order_by(MatchDecision.decided_at.desc(), MatchDecision.id.desc())
        ).all()
    )
    audit_history = []
    for d in decisions[:50]:
        prev_ord = orders_by_id.get(d.previous_order_id) if d.previous_order_id else None
        sel_ord = orders_by_id.get(d.selected_order_id) if d.selected_order_id else None
        audit_history.append(
            {
                "id": d.id,
                "raw_record_id": d.raw_record_id,
                "source_type": d.source_type,
                "decision_type": d.decision_type,
                "previous_status": d.previous_status,
                "new_status": d.new_status,
                "previous_order_number": prev_ord.order_number_raw if prev_ord else None,
                "selected_order_number": sel_ord.order_number_raw if sel_ord else None,
                "note": d.note,
                "decided_by": d.decided_by,
                "decided_at_ist": format_ist(d.decided_at),
            }
        )

    return {
        "counts": counts,
        "items": items,
        "audit_history": audit_history,
        "available_orders": [
            {
                "id": o.id,
                "order_number": o.order_number_raw,
                "customer_name": o.customer_name,
                "city": o.city,
                "order_value_inr": format_paise_to_inr(o.total_amount_paise),
            }
            for o in sorted(orders_by_id.values(), key=lambda x: x.order_number_norm)[:100]
        ],
    }


def execute_nl_query(db: Session, workspace_id: str, question: str) -> dict[str, Any]:
    """Translate a natural-language question and execute its validated structured query."""
    settings = get_or_create_workspace_settings(db, workspace_id)
    ref_utc = get_workspace_reference_utc(settings)

    structured: StructuredOrderQuery = translate_natural_language_question(
        question=question,
        unshipped_threshold_hours=settings.unshipped_threshold_hours,
        stale_tracking_threshold_hours=settings.stale_tracking_threshold_hours,
    )

    if structured.intent == "ambiguous_clarification":
        return {
            "question": question,
            "intent": structured.intent,
            "is_ambiguous": True,
            "is_unsupported": False,
            "explanation": structured.explanation,
            "parser_used": structured.parser_used,
            "reference_time_ist": format_ist(ref_utc),
            "filters_applied": {},
            "clarification_choices": [c.model_dump() for c in structured.clarification_choices],
            "results": [],
            "total_results": 0,
            "orders_page_filter_params": {},
        }

    if structured.intent == "unsupported_question":
        return {
            "question": question,
            "intent": structured.intent,
            "is_ambiguous": False,
            "is_unsupported": True,
            "explanation": structured.explanation,
            "parser_used": structured.parser_used,
            "reference_time_ist": format_ist(ref_utc),
            "filters_applied": {},
            "clarification_choices": [],
            "results": [],
            "total_results": 0,
            "orders_page_filter_params": {},
        }

    if structured.intent == "review_unmatched_records":
        review_data = get_match_review_queue(db, workspace_id, status_filter="actionable")
        # Also return orders marked as Needs review so user can view either Match Review or Orders table
        orders_res = list_orders(
            db,
            workspace_id,
            match_status="Needs review",
            page=1,
            page_size=structured.limit,
        )
        return {
            "question": question,
            "intent": structured.intent,
            "is_ambiguous": False,
            "is_unsupported": False,
            "explanation": structured.explanation,
            "parser_used": structured.parser_used,
            "reference_time_ist": format_ist(ref_utc),
            "filters_applied": {
                "Queue": "Unmatched & Needs Review Source Records",
                "Orders Match Status": "Needs review",
            },
            "clarification_choices": [],
            "results": orders_res["items"],
            "total_results": orders_res["total"],
            "unmatched_source_records": review_data["items"],
            "unmatched_counts": review_data["counts"],
            "orders_page_filter_params": {"match_status": "Needs review"},
            "open_in_match_review": True,
        }

    # Standard order filter query
    filters_display: dict[str, str] = {}
    filter_params: dict[str, Any] = {}

    if structured.shipment_status != "any":
        filters_display["Shipment Status"] = structured.shipment_status
        filter_params["shipment_status"] = structured.shipment_status
    if structured.fulfillment_status != "any":
        filters_display["Fulfillment Status"] = structured.fulfillment_status
        filter_params["fulfillment_status"] = structured.fulfillment_status
    if structured.payment_method != "any":
        filters_display["Payment Method"] = structured.payment_method
        filter_params["payment_method"] = structured.payment_method
    if structured.courier:
        filters_display["Courier"] = structured.courier
        filter_params["courier"] = structured.courier
    if structured.city:
        filters_display["City"] = structured.city
        filter_params["city"] = structured.city
    if structured.attention_level != "any":
        filters_display["Attention Level"] = structured.attention_level
        filter_params["attention_level"] = structured.attention_level
    if structured.rule_code != "any":
        filters_display["Operational Rule"] = structured.rule_code
        filter_params["rule_code"] = structured.rule_code
    if structured.min_stale_days is not None:
        filters_display["No Tracking Update For"] = f">= {structured.min_stale_days} days"
        filter_params["min_stale_days"] = structured.min_stale_days
    if structured.min_unshipped_days is not None:
        filters_display["Awaiting Shipment For"] = f">= {structured.min_unshipped_days} days"
        filter_params["min_unshipped_days"] = structured.min_unshipped_days
    if structured.has_open_complaint is not None:
        filters_display["Open Customer Complaint"] = "Yes" if structured.has_open_complaint else "No"
        filter_params["has_open_complaints"] = "true" if structured.has_open_complaint else "false"
    if structured.match_status != "any":
        filters_display["Match Status"] = structured.match_status
        filter_params["match_status"] = structured.match_status

    orders_res = list_orders(
        db,
        workspace_id,
        courier=structured.courier,
        shipment_status="" if structured.shipment_status == "any" else structured.shipment_status,
        fulfillment_status="" if structured.fulfillment_status == "any" else structured.fulfillment_status,
        attention_level="" if structured.attention_level == "any" else structured.attention_level,
        rule_code="" if structured.rule_code == "any" else structured.rule_code,
        has_open_complaints=structured.has_open_complaint,
        payment_method="" if structured.payment_method == "any" else structured.payment_method,
        city=structured.city,
        match_status="" if structured.match_status == "any" else structured.match_status,
        min_stale_days=structured.min_stale_days,
        min_unshipped_days=structured.min_unshipped_days,
        sort_by=structured.sort_by,
        page=1,
        page_size=structured.limit,
    )

    return {
        "question": question,
        "intent": structured.intent,
        "is_ambiguous": False,
        "is_unsupported": False,
        "explanation": structured.explanation,
        "parser_used": structured.parser_used,
        "reference_time_ist": format_ist(ref_utc),
        "filters_applied": filters_display,
        "clarification_choices": [],
        "results": orders_res["items"],
        "total_results": orders_res["total"],
        "orders_page_filter_params": filter_params,
        "open_in_match_review": False,
    }


def export_orders_csv(
    db: Session,
    workspace_id: str,
    **filter_kwargs: Any,
) -> str:
    """Export filtered orders to CSV with spreadsheet formula injection sanitization."""
    bundle = _assemble_workspace_bundle(db, workspace_id)
    ref_utc = bundle["ref_utc"]

    all_rows = [
        _build_order_summary_dict(
            o,
            bundle["shipments_by_order"].get(o.id, []),
            bundle["complaints_by_order"].get(o.id, []),
            bundle["flags_by_order"].get(o.id, []),
            ref_utc,
        )
        for o in bundle["orders"]
    ]

    filtered = filter_order_rows(all_rows, **filter_kwargs)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Order Number",
            "Order Date (IST)",
            "Customer Name",
            "Masked Phone Number",
            "City",
            "Order Value (INR)",
            "Payment Method",
            "Fulfillment Status",
            "Courier",
            "Tracking Number(s)",
            "Latest Shipment Status",
            "Last Tracking Update (IST)",
            "Open Complaints",
            "Attention Level",
            "Reason for Attention",
            "Match Status",
        ]
    )

    for r in filtered:
        writer.writerow(
            [
                sanitize_csv_cell(r["order_number"]),
                sanitize_csv_cell(r["order_date_ist"]),
                sanitize_csv_cell(r["customer_name"]),
                sanitize_csv_cell(r["customer_phone_masked"]),
                sanitize_csv_cell(r["city"]),
                sanitize_csv_cell(r["order_value_inr"]),
                sanitize_csv_cell(r["payment_method"]),
                sanitize_csv_cell(r["fulfillment_status"]),
                sanitize_csv_cell(r["courier"]),
                sanitize_csv_cell(r["tracking_number"]),
                sanitize_csv_cell(r["latest_shipment_status_label"]),
                sanitize_csv_cell(r["last_tracking_update_ist"]),
                sanitize_csv_cell(r["open_complaint_count"]),
                sanitize_csv_cell(r["attention_level"]),
                sanitize_csv_cell(r["reason_for_attention"]),
                sanitize_csv_cell(r["match_status"]),
            ]
        )

    return output.getvalue()
