"""Ingestion service that persists validated CSV batches into raw and normalized tables idempotently."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.attention_rules import get_or_create_workspace_settings
from src.csv_parser import CSVParseResult, parse_and_validate_csv
from src.matching import MATCH_EXACT, reconcile_workspace
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


def _parse_iso_utc(iso_str: str | None) -> datetime | None:
    if not iso_str:
        return None
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def get_existing_fingerprints(db: Session, workspace_id: str, source_type: str) -> set[str]:
    """Return all accepted row fingerprints already stored for a workspace and source_type."""
    rows = db.scalars(
        select(RawSourceRecord.row_fingerprint).where(
            RawSourceRecord.workspace_id == workspace_id,
            RawSourceRecord.source_type == source_type,
            RawSourceRecord.validation_status == "accepted",
        )
    ).all()
    return set(rows)


def ingest_csv_bytes(
    db: Session,
    *,
    workspace_id: str,
    raw_bytes: bytes,
    filename: str,
    source_type: str,
    custom_mapping: dict[str, str] | None = None,
    date_format_preference: str = "DD/MM/YYYY",
    is_demo: bool = False,
) -> dict[str, Any]:
    """Parse, validate, persist, and reconcile a CSV upload idempotently."""
    existing_fps = get_existing_fingerprints(db, workspace_id, source_type)
    parse_result: CSVParseResult = parse_and_validate_csv(
        raw_bytes=raw_bytes,
        filename=filename,
        source_type=source_type,
        custom_mapping=custom_mapping,
        date_format_preference=date_format_preference,
        existing_fingerprints=existing_fps,
    )

    if parse_result.missing_required_fields:
        missing_labels = ", ".join(f["label"] for f in parse_result.missing_required_fields)
        raise ValueError(f"Cannot complete import: missing required column mapping(s): {missing_labels}")

    if parse_result.ambiguous_date_samples and date_format_preference.lower() == "auto":
        sample = parse_result.ambiguous_date_samples[0]
        raise ValueError(
            f"Ambiguous date '{sample['raw_value']}' on row {sample['row_number']}. "
            "Please select an explicit date format (DD/MM/YYYY or MM/DD/YYYY) before importing."
        )

    batch = ImportBatch(
        workspace_id=workspace_id,
        source_type=source_type,
        filename=filename,
        file_hash=parse_result.file_hash,
        is_demo=is_demo,
        date_format_preference=date_format_preference,
        total_rows=parse_result.total_rows,
        accepted_rows=parse_result.accepted_rows,
        rejected_rows=parse_result.rejected_rows,
        duplicate_rows=parse_result.duplicate_rows,
        column_mapping_json=json.dumps(parse_result.column_mapping),
        error_report_json=json.dumps(parse_result.error_rows),
        summary_json="{}",
    )
    db.add(batch)
    db.flush()

    created_orders = 0
    consolidated_line_items = 0
    created_shipments = 0
    created_events = 0
    created_complaints = 0

    # Cache existing normalized records in workspace for fast idempotent lookup
    orders_by_norm: dict[str, Order] = {
        o.order_number_norm: o
        for o in db.scalars(select(Order).where(Order.workspace_id == workspace_id)).all()
    }
    shipments_by_norm: dict[str, Shipment] = {
        s.tracking_number_norm: s
        for s in db.scalars(select(Shipment).where(Shipment.workspace_id == workspace_id)).all()
    }
    complaints_by_key: dict[str, Complaint] = {
        c.complaint_key: c
        for c in db.scalars(select(Complaint).where(Complaint.workspace_id == workspace_id)).all()
    }

    for parsed_row in parse_result.parsed_rows:
        raw_rec = RawSourceRecord(
            workspace_id=workspace_id,
            import_batch_id=batch.id,
            source_type=source_type,
            source_filename=filename,
            row_number=parsed_row.row_number,
            row_fingerprint=parsed_row.row_fingerprint,
            raw_payload_json=json.dumps(parsed_row.raw_row, ensure_ascii=False),
            normalized_payload_json=json.dumps(parsed_row.normalized, ensure_ascii=False),
            validation_status=parsed_row.validation_status,
            validation_errors_json=json.dumps(parsed_row.errors, ensure_ascii=False),
        )
        db.add(raw_rec)
        db.flush()

        if parsed_row.validation_status != "accepted":
            continue

        norm = parsed_row.normalized

        if source_type == "shopify_orders":
            order_norm = norm["order_number_norm"]
            order_dt = _parse_iso_utc(norm.get("order_date_utc")) or datetime.now(timezone.utc)
            promised_dt = _parse_iso_utc(norm.get("promised_delivery_utc"))

            ord_obj = orders_by_norm.get(order_norm)
            if ord_obj is None:
                ord_obj = Order(
                    workspace_id=workspace_id,
                    import_batch_id=batch.id,
                    raw_record_id=raw_rec.id,
                    order_number_raw=norm["order_number_raw"],
                    order_number_norm=order_norm,
                    order_date=order_dt,
                    promised_delivery_date=promised_dt,
                    customer_name=norm.get("customer_name", ""),
                    customer_phone_raw=norm.get("customer_phone_raw", ""),
                    customer_phone_norm=norm.get("customer_phone_norm", ""),
                    customer_phone_masked=norm.get("customer_phone_masked", ""),
                    customer_email=norm.get("customer_email", ""),
                    city=norm.get("city", ""),
                    state=norm.get("state", ""),
                    pincode=norm.get("pincode", ""),
                    total_amount_paise=int(norm.get("total_amount_paise", 0)),
                    currency="INR",
                    payment_method=norm.get("payment_method", "Prepaid"),
                    financial_status=norm.get("financial_status", "paid"),
                    fulfillment_status=norm.get("fulfillment_status", "unfulfilled"),
                    is_cancelled=bool(norm.get("is_cancelled", False)),
                    source_filename=filename,
                    source_row_number=parsed_row.row_number,
                    match_status=MATCH_EXACT,
                )
                db.add(ord_obj)
                db.flush()
                orders_by_norm[order_norm] = ord_obj
                created_orders += 1
            else:
                # Consolidate multi-line-item Shopify rows onto the same Order without double-counting the order!
                if not ord_obj.customer_name and norm.get("customer_name"):
                    ord_obj.customer_name = norm["customer_name"]
                if not ord_obj.customer_phone_norm and norm.get("customer_phone_norm"):
                    ord_obj.customer_phone_raw = norm.get("customer_phone_raw", "")
                    ord_obj.customer_phone_norm = norm["customer_phone_norm"]
                    ord_obj.customer_phone_masked = norm.get("customer_phone_masked", "")
                if not ord_obj.city and norm.get("city"):
                    ord_obj.city = norm["city"]
                # If one row is fulfilled and another is unfulfilled, mark order as partial fulfillment
                row_ff = norm.get("fulfillment_status", "unfulfilled")
                if ord_obj.fulfillment_status != row_ff and not ord_obj.is_cancelled:
                    if {ord_obj.fulfillment_status, row_ff} & {"fulfilled", "partial"}:
                        ord_obj.fulfillment_status = "partial"

            # Add consolidated OrderLineItem idempotently
            li_key_raw = f"{norm.get('sku', '')}|{norm.get('product_title', '')}|{norm.get('tracking_number_norm', '')}|{parsed_row.row_fingerprint[:12]}"
            li_key = hashlib.sha256(li_key_raw.encode("utf-8")).hexdigest()[:32]
            existing_li = db.scalar(
                select(OrderLineItem).where(
                    OrderLineItem.order_id == ord_obj.id,
                    OrderLineItem.line_item_key == li_key,
                )
            )
            if not existing_li:
                db.add(
                    OrderLineItem(
                        workspace_id=workspace_id,
                        order_id=ord_obj.id,
                        raw_record_id=raw_rec.id,
                        line_item_key=li_key,
                        sku=norm.get("sku", ""),
                        product_title=norm.get("product_title", "Standard Item"),
                        quantity=int(norm.get("quantity", 1)),
                        unit_price_paise=int(norm.get("unit_price_paise", 0)),
                        fulfillment_status=norm.get("fulfillment_status", "unfulfilled"),
                        tracking_number_raw=norm.get("tracking_number_raw", ""),
                        courier_name=norm.get("courier_name", ""),
                        source_filename=filename,
                        source_row_number=parsed_row.row_number,
                    )
                )
                consolidated_line_items += 1

        elif source_type == "courier_tracking":
            tracking_norm = norm["tracking_number_norm"]
            ev_dt = _parse_iso_utc(norm.get("event_timestamp_utc")) or datetime.now(timezone.utc)
            edd_dt = _parse_iso_utc(norm.get("expected_delivery_utc"))

            shp_obj = shipments_by_norm.get(tracking_norm)
            if shp_obj is None:
                shp_obj = Shipment(
                    workspace_id=workspace_id,
                    import_batch_id=batch.id,
                    raw_record_id=raw_rec.id,
                    tracking_number_raw=norm["tracking_number_raw"],
                    tracking_number_norm=tracking_norm,
                    courier_name=norm.get("courier_name") or "Standard Courier",
                    order_number_ref_raw=norm.get("order_number_ref_raw", ""),
                    order_number_ref_norm=norm.get("order_number_ref_norm", ""),
                    customer_phone_raw=norm.get("customer_phone_raw", ""),
                    customer_phone_norm=norm.get("customer_phone_norm", ""),
                    current_status_raw=norm["status_raw"],
                    current_status_norm=norm["status_norm"],
                    latest_event_at=ev_dt,
                    latest_location=norm.get("location", ""),
                    expected_delivery_date=edd_dt,
                    failed_attempts_count=int(norm.get("delivery_attempts", 0)),
                    source_filename=filename,
                    source_row_number=parsed_row.row_number,
                )
                db.add(shp_obj)
                db.flush()
                shipments_by_norm[tracking_norm] = shp_obj
                created_shipments += 1
            else:
                # Update supplementary metadata if missing on existing shipment
                if not shp_obj.order_number_ref_norm and norm.get("order_number_ref_norm"):
                    shp_obj.order_number_ref_raw = norm.get("order_number_ref_raw", "")
                    shp_obj.order_number_ref_norm = norm["order_number_ref_norm"]
                if not shp_obj.customer_phone_norm and norm.get("customer_phone_norm"):
                    shp_obj.customer_phone_raw = norm.get("customer_phone_raw", "")
                    shp_obj.customer_phone_norm = norm["customer_phone_norm"]
                if edd_dt and not shp_obj.expected_delivery_date:
                    shp_obj.expected_delivery_date = edd_dt

            # Add ShipmentEvent idempotently
            ev_key_str = f"{tracking_norm}|{norm['status_norm']}|{ev_dt.isoformat()}"
            ev_key = hashlib.sha256(ev_key_str.encode("utf-8")).hexdigest()[:32]
            existing_ev = db.scalar(
                select(ShipmentEvent).where(
                    ShipmentEvent.shipment_id == shp_obj.id,
                    ShipmentEvent.event_key == ev_key,
                )
            )
            if not existing_ev:
                db.add(
                    ShipmentEvent(
                        workspace_id=workspace_id,
                        shipment_id=shp_obj.id,
                        raw_record_id=raw_rec.id,
                        event_key=ev_key,
                        status_raw=norm["status_raw"],
                        status_norm=norm["status_norm"],
                        event_at=ev_dt,
                        location=norm.get("location", ""),
                        remarks=norm.get("remarks", ""),
                        delivery_attempts=int(norm.get("delivery_attempts", 0)),
                        source_filename=filename,
                        source_row_number=parsed_row.row_number,
                    )
                )
                created_events += 1

        elif source_type == "whatsapp_complaints":
            comp_key = parsed_row.row_fingerprint
            comp_dt = _parse_iso_utc(norm.get("complaint_timestamp_utc")) or datetime.now(timezone.utc)
            if comp_key not in complaints_by_key:
                comp_obj = Complaint(
                    workspace_id=workspace_id,
                    import_batch_id=batch.id,
                    raw_record_id=raw_rec.id,
                    complaint_key=comp_key,
                    complaint_ref=norm.get("complaint_ref", ""),
                    customer_name=norm.get("customer_name", ""),
                    customer_phone_raw=norm.get("customer_phone_raw", ""),
                    customer_phone_norm=norm.get("customer_phone_norm", ""),
                    customer_phone_masked=norm.get("customer_phone_masked", ""),
                    order_number_mentioned_raw=norm.get("order_number_mentioned_raw", ""),
                    order_number_mentioned_norm=norm.get("order_number_mentioned_norm", ""),
                    tracking_mentioned_norm=norm.get("tracking_mentioned_norm", ""),
                    complaint_at=comp_dt,
                    channel="WhatsApp CSV Import",
                    issue_category=norm.get("issue_category", "Delivery Inquiry"),
                    message_text=norm["message_text"],
                    status=norm.get("status", "Open"),
                    claims_not_received=bool(norm.get("claims_not_received", False)),
                    source_filename=filename,
                    source_row_number=parsed_row.row_number,
                )
                db.add(comp_obj)
                db.flush()
                complaints_by_key[comp_key] = comp_obj
                created_complaints += 1

    settings = get_or_create_workspace_settings(db, workspace_id)
    settings.last_imported_at = datetime.now(timezone.utc)
    if not is_demo:
        # If user uploads custom files, record whether workspace is still pure demo or custom
        pass

    db.flush()
    reconcile_summary = reconcile_workspace(db, workspace_id)

    summary_payload = {
        "batch_id": batch.id,
        "source_type": source_type,
        "filename": filename,
        "total_rows": batch.total_rows,
        "accepted_rows": batch.accepted_rows,
        "rejected_rows": batch.rejected_rows,
        "duplicate_rows": batch.duplicate_rows,
        "created_orders": created_orders,
        "consolidated_line_items": consolidated_line_items,
        "created_shipments": created_shipments,
        "created_events": created_events,
        "created_complaints": created_complaints,
        "reconciliation": reconcile_summary,
    }
    batch.summary_json = json.dumps(summary_payload)
    db.commit()
    return summary_payload


def delete_import_batch(db: Session, workspace_id: str, batch_id: int) -> dict[str, Any]:
    """Delete an import batch and its associated records, then re-reconcile the workspace."""
    batch = db.scalar(
        select(ImportBatch).where(
            ImportBatch.id == batch_id,
            ImportBatch.workspace_id == workspace_id,
        )
    )
    if not batch:
        raise ValueError(f"Import batch #{batch_id} not found.")

    raw_ids = list(
        db.scalars(
            select(RawSourceRecord.id).where(
                RawSourceRecord.import_batch_id == batch_id,
                RawSourceRecord.workspace_id == workspace_id,
            )
        ).all()
    )

    if batch.source_type == "shopify_orders":
        db.execute(
            delete(Order).where(
                Order.import_batch_id == batch_id,
                Order.workspace_id == workspace_id,
            )
        )
    elif batch.source_type == "courier_tracking":
        if raw_ids:
            db.execute(
                delete(ShipmentEvent).where(
                    ShipmentEvent.raw_record_id.in_(raw_ids),
                    ShipmentEvent.workspace_id == workspace_id,
                )
            )
        db.execute(
            delete(Shipment).where(
                Shipment.import_batch_id == batch_id,
                Shipment.workspace_id == workspace_id,
            )
        )
    elif batch.source_type == "whatsapp_complaints":
        db.execute(
            delete(Complaint).where(
                Complaint.import_batch_id == batch_id,
                Complaint.workspace_id == workspace_id,
            )
        )

    db.delete(batch)
    db.flush()

    reconcile_summary = reconcile_workspace(db, workspace_id)
    return {
        "deleted_batch_id": batch_id,
        "source_type": batch.source_type,
        "filename": batch.filename,
        "reconciliation": reconcile_summary,
    }


def reset_workspace_data(db: Session, workspace_id: str) -> None:
    """Clear all imported data for a workspace (used before reloading a clean demo dataset)."""
    db.execute(delete(AttentionFlag).where(AttentionFlag.workspace_id == workspace_id))
    db.execute(delete(MatchDecision).where(MatchDecision.workspace_id == workspace_id))
    db.execute(delete(ShipmentEvent).where(ShipmentEvent.workspace_id == workspace_id))
    db.execute(delete(Shipment).where(Shipment.workspace_id == workspace_id))
    db.execute(delete(Complaint).where(Complaint.workspace_id == workspace_id))
    db.execute(delete(OrderLineItem).where(OrderLineItem.workspace_id == workspace_id))
    db.execute(delete(Order).where(Order.workspace_id == workspace_id))
    db.execute(delete(RawSourceRecord).where(RawSourceRecord.workspace_id == workspace_id))
    db.execute(delete(ImportBatch).where(ImportBatch.workspace_id == workspace_id))
    db.commit()
