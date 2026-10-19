"""Transparent operational attention rules engine for Order Ops Console.

An attention level ('High', 'Medium', 'Low', 'No current flag') represents operational
priority for the team—never a pretend machine-learning probability.

Rules implemented:
1. DELIVERED_NOT_RECEIVED (High): Customer complaint reports order not received, while courier status is 'Delivered'.
2. CONFLICTING_SHIPMENT_INFO (High): Contradictory courier event timeline (e.g., transit scan after delivery/RTO) or split shipment conflict.
3. RTO_INITIATED (High): Courier initiated Return-to-Origin (RTO) or returned parcel to warehouse.
4. COMPLAINT_UNDELIVERED (High): Open customer complaint attached to an undelivered order.
5. FAILED_DELIVERY_ATTEMPT (High / Medium): Courier recorded 1+ failed delivery attempts (NDR) on an undelivered shipment.
6. STALE_TRACKING (High / Medium): Active shipment ('awaiting_pickup', 'in_transit', 'out_for_delivery') has had no tracking scan for >= stale_tracking_threshold_hours (default 72h / 3 days).
7. UNSHIPPED_DELAY (High / Medium): Non-cancelled order has no shipment / remains unfulfilled for >= unshipped_threshold_hours (default 48h / 2 days).
8. PAST_EXPECTED_DELIVERY (Medium): Undelivered, non-cancelled shipment is past its expected/promised delivery date.
9. UNKNOWN_COURIER_STATUS (Low): Courier status string could not be mapped to a standard lifecycle stage.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.config import IST, get_config
from src.models import (
    AttentionFlag,
    Complaint,
    OperationalSettings,
    Order,
    Shipment,
)
from src.normalization import format_ist, format_shipment_status_label

LEVEL_HIGH = "High"
LEVEL_MEDIUM = "Medium"
LEVEL_LOW = "Low"
LEVEL_NONE = "No current flag"

_PRIORITY_RANK = {
    LEVEL_HIGH: 3,
    LEVEL_MEDIUM: 2,
    LEVEL_LOW: 1,
    LEVEL_NONE: 0,
}

# Active transit statuses eligible for stale-tracking checks.
# Delivered, returned (rto_delivered / rto_initiated), and cancelled shipments are NEVER treated as moving in transit.
ACTIVE_MOVING_STATUSES = ("awaiting_pickup", "in_transit", "out_for_delivery")
TERMINAL_STATUSES = ("delivered", "rto_delivered", "cancelled")


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def get_or_create_workspace_settings(db: Session, workspace_id: str) -> OperationalSettings:
    """Retrieve or initialize per-workspace operational thresholds and reference time."""
    settings = db.scalar(
        select(OperationalSettings).where(OperationalSettings.workspace_id == workspace_id)
    )
    if not settings:
        cfg = get_config()
        settings = OperationalSettings(
            workspace_id=workspace_id,
            reference_time_mode=cfg.reference_time_mode,
            demo_reference_iso=cfg.demo_reference_time,
            unshipped_threshold_hours=cfg.threshold_unshipped_hours,
            stale_tracking_threshold_hours=cfg.threshold_stale_tracking_hours,
            expected_delivery_grace_hours=0,
            is_demo_dataset=(workspace_id == cfg.default_workspace_id),
        )
        db.add(settings)
        db.flush()
    return settings


def get_workspace_reference_utc(settings: OperationalSettings) -> datetime:
    """Resolve the active reference timestamp in UTC for time-based evaluations."""
    if settings.reference_time_mode == "live_ist":
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(settings.demo_reference_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return get_config().get_default_reference_utc()


def _format_hours_days(hours: float) -> str:
    days = hours / 24.0
    if abs(days - round(days)) < 0.15 and round(days) >= 1:
        d = int(round(days))
        return f"{d} day{'s' if d != 1 else ''} ({int(round(hours))}h)"
    if days >= 1.0:
        return f"{days:.1f} days ({int(round(hours))}h)"
    return f"{int(round(hours))} hours"


def evaluate_order_flags(
    order: Order,
    shipments: list[Shipment],
    complaints: list[Complaint],
    reference_utc: datetime,
    unshipped_threshold_hours: int = 48,
    stale_tracking_threshold_hours: int = 72,
) -> list[dict[str, Any]]:
    """Evaluate all operational attention rules for a single Order and its linked records."""
    flags: list[dict[str, Any]] = []
    ref_utc = _ensure_utc(reference_utc) or datetime.now(timezone.utc)

    # Cancelled orders are NEVER flagged as awaiting shipment or in transit.
    is_order_cancelled = bool(order.is_cancelled or order.fulfillment_status == "cancelled")
    open_complaints = [c for c in complaints if c.status.lower() != "resolved"]
    active_shipments = [s for s in shipments if s.current_status_norm != "cancelled"]

    all_shipments_delivered = bool(
        active_shipments and all(s.current_status_norm == "delivered" for s in active_shipments)
    )
    any_shipment_delivered = any(s.current_status_norm == "delivered" for s in active_shipments)

    # -------------------------------------------------------------------------
    # Rule 1: Complaint saying "not received" when courier status says "delivered"
    # -------------------------------------------------------------------------
    if any_shipment_delivered:
        dnr_complaints = [c for c in open_complaints if c.claims_not_received]
        if dnr_complaints:
            comp = dnr_complaints[0]
            dlv_shipments = [s for s in active_shipments if s.current_status_norm == "delivered"]
            dlv_shp = dlv_shipments[0]
            flags.append(
                {
                    "rule_code": "DELIVERED_NOT_RECEIVED",
                    "rule_name": "Delivered Status Disputed by Customer",
                    "attention_level": LEVEL_HIGH,
                    "triggering_evidence": (
                        f"Courier ({dlv_shp.courier_name} AWB {dlv_shp.tracking_number_raw}) marked 'Delivered' on "
                        f"{format_ist(dlv_shp.latest_event_at)}, but customer complained on {format_ist(comp.complaint_at)}: "
                        f"\"{comp.message_text}\""
                    ),
                    "threshold_used": "Immediate flag on delivery contradiction",
                    "explanation": (
                        f"Courier {dlv_shp.courier_name} reports AWB {dlv_shp.tracking_number_raw} as Delivered, "
                        "but the customer filed a complaint stating the parcel was not received."
                    ),
                    "suggested_action": (
                        "Request Proof of Delivery (POD) and delivery OTP/signature from the courier, "
                        "and contact the customer to verify whether a neighbor or security guard received the package."
                    ),
                    "source_references": [
                        {"source": dlv_shp.source_filename, "row": dlv_shp.source_row_number, "type": "courier_tracking"},
                        {"source": comp.source_filename, "row": comp.source_row_number, "type": "whatsapp_complaints"},
                    ],
                }
            )

    # -------------------------------------------------------------------------
    # Rule 2: Conflicting shipment information
    # -------------------------------------------------------------------------
    for shp in shipments:
        if shp.has_conflicting_status:
            flags.append(
                {
                    "rule_code": "CONFLICTING_SHIPMENT_INFO",
                    "rule_name": "Conflicting Shipment Scan History",
                    "attention_level": LEVEL_HIGH,
                    "triggering_evidence": (
                        f"AWB {shp.tracking_number_raw} ({shp.courier_name}): {shp.conflict_explanation}"
                    ),
                    "threshold_used": "Strict chronological lifecycle check",
                    "explanation": (
                        f"Shipment {shp.tracking_number_raw} has contradictory tracking scans ({shp.conflict_explanation})"
                    ),
                    "suggested_action": (
                        "Verify the actual physical status of AWB "
                        f"{shp.tracking_number_raw} on the {shp.courier_name} partner portal and confirm with the warehouse."
                    ),
                    "source_references": [
                        {"source": shp.source_filename, "row": shp.source_row_number, "type": "courier_tracking"}
                    ],
                }
            )

    # Also check if a cancelled order still has an active moving shipment!
    if is_order_cancelled:
        moving_on_cancelled = [
            s for s in active_shipments if s.current_status_norm in ("in_transit", "out_for_delivery", "failed_attempt")
        ]
        if moving_on_cancelled:
            shp = moving_on_cancelled[0]
            flags.append(
                {
                    "rule_code": "CONFLICTING_SHIPMENT_INFO",
                    "rule_name": "Cancelled Order Has Active Shipment",
                    "attention_level": LEVEL_HIGH,
                    "triggering_evidence": (
                        f"Order {order.order_number_raw} is marked Cancelled in Shopify, but AWB {shp.tracking_number_raw} "
                        f"({shp.courier_name}) has status '{shp.current_status_raw}'."
                    ),
                    "threshold_used": "Order cancellation vs. active courier dispatch check",
                    "explanation": (
                        f"Order {order.order_number_raw} was cancelled, yet shipment {shp.tracking_number_raw} is still moving with {shp.courier_name}."
                    ),
                    "suggested_action": (
                        f"Raise an immediate recall / RTO request with {shp.courier_name} for AWB {shp.tracking_number_raw} so the cancelled order is not delivered."
                    ),
                    "source_references": [
                        {"source": order.source_filename, "row": order.source_row_number, "type": "shopify_orders"},
                        {"source": shp.source_filename, "row": shp.source_row_number, "type": "courier_tracking"},
                    ],
                }
            )

    # -------------------------------------------------------------------------
    # Rule 3: Return-to-Origin (RTO) initiated or returned to warehouse
    # -------------------------------------------------------------------------
    rto_shipments = [
        s for s in active_shipments if s.current_status_norm in ("rto_initiated", "rto_delivered")
    ]
    for shp in rto_shipments:
        is_completed_rto = shp.current_status_norm == "rto_delivered"
        flags.append(
            {
                "rule_code": "RTO_INITIATED",
                "rule_name": "Return to Origin (RTO)",
                "attention_level": LEVEL_HIGH if not is_completed_rto or order.payment_method == "Prepaid" else LEVEL_MEDIUM,
                "triggering_evidence": (
                    f"AWB {shp.tracking_number_raw} ({shp.courier_name}) marked '{shp.current_status_raw}' "
                    f"at {shp.latest_location or 'Hub'} on {format_ist(shp.latest_event_at)}."
                ),
                "threshold_used": "Any RTO status (RTO Initiated / RTO Delivered)",
                "explanation": (
                    f"Shipment {shp.tracking_number_raw} is returning to the warehouse ({shp.current_status_raw}) "
                    f"after {shp.failed_attempts_count} failed delivery attempt(s)."
                ),
                "suggested_action": (
                    "Check the return shipment tracking until warehouse receipt; if this is a Prepaid order, "
                    "contact the customer to confirm whether to re-ship or refund once received."
                ),
                "source_references": [
                    {"source": shp.source_filename, "row": shp.source_row_number, "type": "courier_tracking"}
                ],
            }
        )

    # -------------------------------------------------------------------------
    # Rule 4: Open Customer Complaint on an Undelivered Order
    # -------------------------------------------------------------------------
    if open_complaints and not all_shipments_delivered:
        for comp in open_complaints:
            flags.append(
                {
                    "rule_code": "COMPLAINT_UNDELIVERED",
                    "rule_name": "Customer Complaint on Undelivered Order",
                    "attention_level": LEVEL_HIGH,
                    "triggering_evidence": (
                        f"WhatsApp complaint ({comp.complaint_ref}) from {comp.customer_phone_masked} on "
                        f"{format_ist(comp.complaint_at)}: \"{comp.message_text}\""
                    ),
                    "threshold_used": "Open customer complaint while order is not delivered",
                    "explanation": (
                        f"Customer reached out via WhatsApp ({comp.issue_category}) while the order is still undelivered."
                    ),
                    "suggested_action": (
                        "Review the unresolved complaint, check the latest courier scan, and send a clear status update to the customer."
                    ),
                    "source_references": [
                        {"source": comp.source_filename, "row": comp.source_row_number, "type": "whatsapp_complaints"}
                    ],
                }
            )

    # -------------------------------------------------------------------------
    # Rule 5: Failed Delivery Attempts (NDR)
    # -------------------------------------------------------------------------
    for shp in active_shipments:
        if shp.current_status_norm == "failed_attempt" or (
            shp.failed_attempts_count > 0 and shp.current_status_norm not in TERMINAL_STATUSES and shp.current_status_norm not in ("rto_initiated",)
        ):
            attempts = max(1, shp.failed_attempts_count)
            level = LEVEL_HIGH if (attempts >= 2 or order.payment_method == "COD") else LEVEL_MEDIUM
            flags.append(
                {
                    "rule_code": "FAILED_DELIVERY_ATTEMPT",
                    "rule_name": "Failed Delivery Attempt (NDR)",
                    "attention_level": level,
                    "triggering_evidence": (
                        f"AWB {shp.tracking_number_raw} ({shp.courier_name}) has {attempts} failed attempt(s); "
                        f"latest status '{shp.current_status_raw}' on {format_ist(shp.latest_event_at)}."
                    ),
                    "threshold_used": "1+ failed delivery attempt (High priority if COD or 2+ attempts)",
                    "explanation": (
                        f"{shp.courier_name} recorded {attempts} failed delivery attempt(s) for AWB {shp.tracking_number_raw} "
                        f"({order.payment_method} order). High risk of RTO if unaddressed."
                    ),
                    "suggested_action": (
                        "Contact the customer to confirm availability, landmark, and alternate phone number, "
                        f"then submit re-attempt instructions to {shp.courier_name}."
                    ),
                    "source_references": [
                        {"source": shp.source_filename, "row": shp.source_row_number, "type": "courier_tracking"}
                    ],
                }
            )

    # -------------------------------------------------------------------------
    # Rule 6: Stale Tracking in Transit
    # (Only applies to active moving shipments; never delivered, returned, or cancelled)
    # -------------------------------------------------------------------------
    for shp in active_shipments:
        if shp.current_status_norm in ACTIVE_MOVING_STATUSES and shp.latest_event_at:
            ev_utc = _ensure_utc(shp.latest_event_at)
            if ev_utc and ref_utc > ev_utc:
                elapsed_hours = (ref_utc - ev_utc).total_seconds() / 3600.0
                if elapsed_hours >= stale_tracking_threshold_hours:
                    elapsed_days = int(round(elapsed_hours / 24.0))
                    thresh_days = int(round(stale_tracking_threshold_hours / 24.0))
                    level = LEVEL_HIGH if elapsed_hours >= (stale_tracking_threshold_hours + 48) else LEVEL_MEDIUM
                    flags.append(
                        {
                            "rule_code": "STALE_TRACKING",
                            "rule_name": "Stale Tracking Update",
                            "attention_level": level,
                            "triggering_evidence": (
                                f"AWB {shp.tracking_number_raw} ({shp.courier_name}) last scanned at "
                                f"'{shp.latest_location or 'Hub'}' ({shp.current_status_raw}) on {format_ist(ev_utc)}."
                            ),
                            "threshold_used": f"{thresh_days} days ({stale_tracking_threshold_hours} hours) without scan",
                            "explanation": (
                                f"No tracking update for {elapsed_days} days ({int(round(elapsed_hours))} hours). "
                                f"Your configured threshold is {thresh_days} days ({stale_tracking_threshold_hours} hours)."
                            ),
                            "suggested_action": (
                                f"Contact {shp.courier_name} regarding missing scans on AWB {shp.tracking_number_raw} "
                                f"at {shp.latest_location or 'last hub'}."
                            ),
                            "source_references": [
                                {"source": shp.source_filename, "row": shp.source_row_number, "type": "courier_tracking"}
                            ],
                        }
                    )

    # -------------------------------------------------------------------------
    # Rule 7: Paid / Confirmed Order Not Shipped After Threshold
    # (Never flags cancelled orders!)
    # -------------------------------------------------------------------------
    if not is_order_cancelled and (not active_shipments or order.fulfillment_status == "unfulfilled"):
        # If there are no active shipments yet (or only awaiting_pickup without dispatch)
        has_dispatched = any(
            s.current_status_norm in ("in_transit", "out_for_delivery", "failed_attempt", "delivered", "rto_initiated", "rto_delivered")
            for s in active_shipments
        )
        if not has_dispatched and order.order_date:
            ord_utc = _ensure_utc(order.order_date)
            if ord_utc and ref_utc > ord_utc:
                wait_hours = (ref_utc - ord_utc).total_seconds() / 3600.0
                if wait_hours >= unshipped_threshold_hours:
                    wait_days = int(round(wait_hours / 24.0))
                    thresh_days = int(round(unshipped_threshold_hours / 24.0))
                    level = LEVEL_HIGH if wait_hours >= (unshipped_threshold_hours + 48) else LEVEL_MEDIUM
                    flags.append(
                        {
                            "rule_code": "UNSHIPPED_DELAY",
                            "rule_name": "Order Awaiting Shipment Past Threshold",
                            "attention_level": level,
                            "triggering_evidence": (
                                f"Order {order.order_number_raw} placed on {format_ist(ord_utc)} "
                                f"(Fulfillment: {order.fulfillment_status}, Payment: {order.payment_method}) has not been dispatched."
                            ),
                            "threshold_used": f"{thresh_days} days ({unshipped_threshold_hours} hours) dispatch SLA",
                            "explanation": (
                                f"Order has been awaiting shipment for {wait_days} days ({int(round(wait_hours))} hours). "
                                f"Your configured threshold is {thresh_days} days ({unshipped_threshold_hours} hours)."
                            ),
                            "suggested_action": (
                                "Check warehouse packing queue and confirm whether inventory is available or courier pickup was missed."
                            ),
                            "source_references": [
                                {"source": order.source_filename, "row": order.source_row_number, "type": "shopify_orders"}
                            ],
                        }
                    )

    # -------------------------------------------------------------------------
    # Rule 8: Shipment Past Expected Delivery Date
    # -------------------------------------------------------------------------
    if not is_order_cancelled and not all_shipments_delivered:
        for shp in active_shipments:
            if shp.current_status_norm in TERMINAL_STATUSES or shp.current_status_norm == "rto_initiated":
                continue
            edd_utc = _ensure_utc(shp.expected_delivery_date or order.promised_delivery_date)
            if edd_utc and ref_utc > edd_utc:
                overdue_hours = (ref_utc - edd_utc).total_seconds() / 3600.0
                if overdue_hours >= 6:
                    flags.append(
                        {
                            "rule_code": "PAST_EXPECTED_DELIVERY",
                            "rule_name": "Past Expected Delivery Date",
                            "attention_level": LEVEL_MEDIUM,
                            "triggering_evidence": (
                                f"AWB {shp.tracking_number_raw} expected by {format_ist(edd_utc, include_time=False)}, "
                                f"currently '{format_shipment_status_label(shp.current_status_norm)}'."
                            ),
                            "threshold_used": f"Expected Delivery Date ({format_ist(edd_utc, include_time=False)})",
                            "explanation": (
                                f"Shipment {shp.tracking_number_raw} is past its expected delivery date by "
                                f"{_format_hours_days(overdue_hours)}."
                            ),
                            "suggested_action": (
                                f"Proactively notify the customer of the revised delivery estimate and check hub movement with {shp.courier_name}."
                            ),
                            "source_references": [
                                {"source": shp.source_filename, "row": shp.source_row_number, "type": "courier_tracking"}
                            ],
                        }
                    )

    # -------------------------------------------------------------------------
    # Rule 9: Unknown Courier Status
    # -------------------------------------------------------------------------
    for shp in active_shipments:
        if shp.current_status_norm == "unknown":
            flags.append(
                {
                    "rule_code": "UNKNOWN_COURIER_STATUS",
                    "rule_name": "Unrecognized Courier Status Code",
                    "attention_level": LEVEL_LOW,
                    "triggering_evidence": (
                        f"AWB {shp.tracking_number_raw} ({shp.courier_name}) reported unrecognized status '{shp.current_status_raw}'."
                    ),
                    "threshold_used": "Standard courier status dictionary mapping",
                    "explanation": (
                        f"Courier status '{shp.current_status_raw}' is not recognized as a standard transit, delivery, or return state."
                    ),
                    "suggested_action": (
                        f"Inspect row {shp.source_row_number} in {shp.source_filename} and check {shp.courier_name} tracking portal."
                    ),
                    "source_references": [
                        {"source": shp.source_filename, "row": shp.source_row_number, "type": "courier_tracking"}
                    ],
                }
            )

    # Sort flags by priority (High -> Medium -> Low)
    flags.sort(key=lambda f: _PRIORITY_RANK.get(f["attention_level"], 0), reverse=True)
    return flags


def evaluate_workspace_attention_flags(db: Session, workspace_id: str) -> dict[str, int]:
    """Re-evaluate all operational attention flags across all orders in a workspace."""
    settings = get_or_create_workspace_settings(db, workspace_id)
    ref_utc = get_workspace_reference_utc(settings)

    # Clear existing flags in workspace and rebuild deterministically
    db.execute(delete(AttentionFlag).where(AttentionFlag.workspace_id == workspace_id))

    orders = list(db.scalars(select(Order).where(Order.workspace_id == workspace_id)).all())
    shipments = list(db.scalars(select(Shipment).where(Shipment.workspace_id == workspace_id)).all())
    complaints = list(db.scalars(select(Complaint).where(Complaint.workspace_id == workspace_id)).all())

    shipments_by_order: dict[int, list[Shipment]] = {}
    for s in shipments:
        if s.order_id:
            shipments_by_order.setdefault(s.order_id, []).append(s)

    complaints_by_order: dict[int, list[Complaint]] = {}
    for c in complaints:
        if c.order_id:
            complaints_by_order.setdefault(c.order_id, []).append(c)

    flagged_orders_count = 0
    total_flags_count = 0

    for ord_obj in orders:
        ord_shipments = shipments_by_order.get(ord_obj.id, [])
        ord_complaints = complaints_by_order.get(ord_obj.id, [])
        evaluated = evaluate_order_flags(
            order=ord_obj,
            shipments=ord_shipments,
            complaints=ord_complaints,
            reference_utc=ref_utc,
            unshipped_threshold_hours=settings.unshipped_threshold_hours,
            stale_tracking_threshold_hours=settings.stale_tracking_threshold_hours,
        )

        if evaluated:
            flagged_orders_count += 1
            ord_obj.attention_level = evaluated[0]["attention_level"]
            ord_obj.primary_attention_reason = evaluated[0]["explanation"]
            for f_data in evaluated:
                total_flags_count += 1
                db.add(
                    AttentionFlag(
                        workspace_id=workspace_id,
                        order_id=ord_obj.id,
                        rule_code=f_data["rule_code"],
                        rule_name=f_data["rule_name"],
                        attention_level=f_data["attention_level"],
                        triggering_evidence=f_data["triggering_evidence"],
                        threshold_used=f_data["threshold_used"],
                        explanation=f_data["explanation"],
                        suggested_action=f_data["suggested_action"],
                        source_references_json=json.dumps(f_data["source_references"]),
                    )
                )
        else:
            ord_obj.attention_level = LEVEL_NONE
            ord_obj.primary_attention_reason = ""

    db.flush()
    return {
        "flagged_orders": flagged_orders_count,
        "total_flags": total_flags_count,
    }
