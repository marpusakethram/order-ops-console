"""Deterministic, explainable record reconciliation and manual match audit engine.

Hierarchy:
1. Exact normalized order identifier (with no conflicting strong identifiers).
2. Exact tracking number / AWB.
3. Complaint text order number corroborated by supporting evidence (phone/name).
4. Exact normalized phone number + supporting evidence (when phone uniquely identifies one order).
5. Multiple compatible fields / fuzzy candidate suggestions routed to 'Needs review'.

Hard guardrails:
- Never auto-match using only a customer name.
- Never auto-match using only an order amount.
- Shared household phone numbers across multiple orders always trigger 'Needs review'.
- Conflicting strong identifiers (e.g., Order ID points to Order A, Phone/AWB points to Order B) trigger 'Needs review'.
- Late-arriving older courier events are added to the timeline without overwriting a newer shipment status.
- Repeated imports are idempotent.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models import (
    Complaint,
    ImportBatch,
    MatchDecision,
    OperationalSettings,
    Order,
    OrderLineItem,
    RawSourceRecord,
    Shipment,
    ShipmentEvent,
)
from src.normalization import (
    format_ist,
    format_paise_to_inr,
    mask_phone,
)

MATCH_EXACT = "Exact match"
MATCH_STRONG = "Strong match"
MATCH_REVIEW = "Needs review"
MATCH_UNMATCHED = "Unmatched"
MATCH_MANUAL = "Manually confirmed"


def _clean_name_tokens(name: str | None) -> list[str]:
    if not name:
        return []
    cleaned = re.sub(r"[^a-z0-9\s]", " ", str(name).lower())
    return [t for t in cleaned.split() if len(t) > 1]


def _names_compatible(name_a: str | None, name_b: str | None) -> tuple[bool, bool]:
    """Return (is_compatible, is_conflict).

    - If either name is missing, (False, False) -> missing info is not confirmation, nor conflict.
    - If exact match or strong token overlap, (True, False).
    - If both names are present and share no tokens, (False, True).
    """
    tokens_a = _clean_name_tokens(name_a)
    tokens_b = _clean_name_tokens(name_b)
    if not tokens_a or not tokens_b:
        return False, False
    if tokens_a == tokens_b:
        return True, False
    set_a, set_b = set(tokens_a), set(tokens_b)
    overlap = set_a & set_b
    if overlap:
        return True, False
    # Check prefix match on first token (e.g. "Aarav S" vs "Aarav Sharma")
    if tokens_a[0] == tokens_b[0] or (
        len(tokens_a[0]) >= 4 and len(tokens_b[0]) >= 4 and tokens_a[0][:4] == tokens_b[0][:4]
    ):
        return True, False
    return False, True


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _format_candidate(order: Order, score: int, reasons: list[str], method: str) -> dict[str, Any]:
    return {
        "order_id": order.id,
        "order_number": order.order_number_raw,
        "order_date_ist": format_ist(order.order_date, include_time=False),
        "customer_name": order.customer_name,
        "customer_phone_masked": order.customer_phone_masked,
        "city": order.city,
        "total_amount_inr": format_paise_to_inr(order.total_amount_paise),
        "payment_method": order.payment_method,
        "fulfillment_status": order.fulfillment_status,
        "heuristic_score": score,
        "heuristic_note": "Heuristic rule score (0-100), not a statistical probability.",
        "match_method": method,
        "reasons": reasons,
    }


def evaluate_order_match(
    *,
    source_type: str,
    norm_payload: dict[str, Any],
    orders_by_norm_id: dict[str, Order],
    orders_by_tracking: dict[str, Order],
    orders_by_phone: dict[str, list[Order]],
    all_orders: list[Order],
) -> dict[str, Any]:
    """Deterministically evaluate how a courier row or complaint row matches existing Orders.

    Returns:
      {
        "status": MATCH_EXACT | MATCH_STRONG | MATCH_REVIEW | MATCH_UNMATCHED,
        "matched_order": Order | None,
        "method": str,
        "score": int,
        "explanation": str,
        "candidates": list[dict],
      }
    """
    col_order_norm = (
        norm_payload.get("order_number_ref_norm")
        or norm_payload.get("col_order_norm")
        or ""
    ).strip()
    text_order_norm = (norm_payload.get("text_order_norm") or "").strip()
    effective_order_norm = col_order_norm or (norm_payload.get("order_number_mentioned_norm") or "").strip()

    tracking_norm = (
        norm_payload.get("tracking_number_norm")
        or norm_payload.get("tracking_mentioned_norm")
        or ""
    ).strip()
    phone_norm = (norm_payload.get("customer_phone_norm") or "").strip()
    customer_name = (norm_payload.get("customer_name") or "").strip()
    amount_paise = norm_payload.get("cod_amount_paise")
    location_or_city = (norm_payload.get("location") or "").strip()

    event_ts_iso = norm_payload.get("event_timestamp_utc") or norm_payload.get("complaint_timestamp_utc")
    event_dt = None
    if event_ts_iso:
        try:
            event_dt = _ensure_utc(datetime.fromisoformat(event_ts_iso))
        except ValueError:
            event_dt = None

    order_by_id = orders_by_norm_id.get(effective_order_norm) if effective_order_norm else None
    order_by_awb = orders_by_tracking.get(tracking_norm) if tracking_norm else None
    phone_orders = orders_by_phone.get(phone_norm, []) if phone_norm else []

    # -------------------------------------------------------------------------
    # 1. Check for Conflicting Strong Identifiers first
    # -------------------------------------------------------------------------
    if order_by_id and order_by_awb and order_by_id.id != order_by_awb.id:
        candidates = [
            _format_candidate(
                order_by_id,
                55,
                [f"Order reference '{effective_order_norm}' matches {order_by_id.order_number_raw}"],
                "conflicting_identifiers",
            ),
            _format_candidate(
                order_by_awb,
                55,
                [f"Tracking / AWB '{tracking_norm}' is linked to {order_by_awb.order_number_raw}"],
                "conflicting_identifiers",
            ),
        ]
        return {
            "status": MATCH_REVIEW,
            "matched_order": None,
            "method": "conflicting_identifiers",
            "score": 55,
            "explanation": (
                f"Conflicting strong identifiers: Order reference points to {order_by_id.order_number_raw}, "
                f"but AWB '{tracking_norm}' belongs to {order_by_awb.order_number_raw}. "
                "Automatic matching was blocked for manual review."
            ),
            "candidates": candidates,
        }

    if order_by_id and phone_norm and order_by_id.customer_phone_norm and (
        phone_norm != order_by_id.customer_phone_norm
    ):
        candidates = [
            _format_candidate(
                order_by_id,
                58,
                [
                    f"Order ID matches {order_by_id.order_number_raw}",
                    f"However, record phone ({mask_phone(phone_norm)}) differs from order phone ({order_by_id.customer_phone_masked})",
                ],
                "conflicting_identifiers",
            )
        ]
        for po in phone_orders:
            if po.id != order_by_id.id:
                candidates.append(
                    _format_candidate(
                        po,
                        52,
                        [f"Customer phone ({po.customer_phone_masked}) matches this order instead"],
                        "conflicting_identifiers",
                    )
                )
        return {
            "status": MATCH_REVIEW,
            "matched_order": None,
            "method": "conflicting_identifiers",
            "score": 58,
            "explanation": (
                f"Conflicting identifiers: Order reference matches {order_by_id.order_number_raw}, "
                f"but phone {mask_phone(phone_norm)} conflicts with the order's phone ({order_by_id.customer_phone_masked})."
            ),
            "candidates": candidates,
        }

    # -------------------------------------------------------------------------
    # 2. Level 1: Exact Normalized Order Identifier (from structured column)
    # -------------------------------------------------------------------------
    if col_order_norm and order_by_id:
        reasons = [f"Normalized Order ID '{col_order_norm}' matches Order {order_by_id.order_number_raw}"]
        if phone_norm and phone_norm == order_by_id.customer_phone_norm:
            reasons.append(f"Corroborated by matching customer phone ({order_by_id.customer_phone_masked})")
        return {
            "status": MATCH_EXACT,
            "matched_order": order_by_id,
            "method": "exact_order_id",
            "score": 100,
            "explanation": "; ".join(reasons) + ".",
            "candidates": [_format_candidate(order_by_id, 100, reasons, "exact_order_id")],
        }

    # If a structured order ID was provided in the CSV, but no such order exists in Shopify exports!
    if col_order_norm and not order_by_id:
        # Check if phone or AWB suggests a candidate for review, rather than silently ignoring the unknown Order ID
        candidates = []
        if order_by_awb:
            candidates.append(
                _format_candidate(
                    order_by_awb,
                    60,
                    [
                        f"AWB '{tracking_norm}' matches {order_by_awb.order_number_raw}, "
                        f"but CSV Order ID '{col_order_norm}' was not found"
                    ],
                    "unfound_order_id_with_candidate",
                )
            )
        for po in phone_orders[:3]:
            candidates.append(
                _format_candidate(
                    po,
                    55,
                    [
                        f"Phone ({po.customer_phone_masked}) matches {po.order_number_raw}, "
                        f"but CSV Order ID '{col_order_norm}' was not found"
                    ],
                    "unfound_order_id_with_candidate",
                )
            )
        if candidates:
            return {
                "status": MATCH_REVIEW,
                "matched_order": None,
                "method": "conflicting_identifiers",
                "score": 55,
                "explanation": (
                    f"Referenced Order ID '{col_order_norm}' does not exist in imported orders, "
                    "though another identifier matched a candidate order. Needs manual review."
                ),
                "candidates": candidates,
            }

    # -------------------------------------------------------------------------
    # 3. Level 2: Exact Tracking Number / AWB
    # -------------------------------------------------------------------------
    if tracking_norm and order_by_awb:
        if phone_norm and order_by_awb.customer_phone_norm and phone_norm != order_by_awb.customer_phone_norm:
            candidates = [
                _format_candidate(
                    order_by_awb,
                    60,
                    [
                        f"AWB '{tracking_norm}' matches {order_by_awb.order_number_raw}, "
                        f"but phone ({mask_phone(phone_norm)}) differs from order phone ({order_by_awb.customer_phone_masked})"
                    ],
                    "conflicting_identifiers",
                )
            ]
            return {
                "status": MATCH_REVIEW,
                "matched_order": None,
                "method": "conflicting_identifiers",
                "score": 60,
                "explanation": (
                    f"AWB '{tracking_norm}' matches Order {order_by_awb.order_number_raw}, "
                    "but customer phone number conflicts with the order record."
                ),
                "candidates": candidates,
            }

        reasons = [f"Exact match on normalized AWB / Tracking Number '{tracking_norm}'"]
        return {
            "status": MATCH_EXACT,
            "matched_order": order_by_awb,
            "method": "exact_tracking_number",
            "score": 95,
            "explanation": f"Exact match on normalized AWB '{tracking_norm}' linked to Order {order_by_awb.order_number_raw}.",
            "candidates": [_format_candidate(order_by_awb, 95, reasons, "exact_tracking_number")],
        }

    # -------------------------------------------------------------------------
    # 4. Level 3a: Order ID mentioned inside free-text complaint + validation
    # -------------------------------------------------------------------------
    if text_order_norm and order_by_id:
        phone_matches = bool(phone_norm and phone_norm == order_by_id.customer_phone_norm)
        name_compat, name_conflict = _names_compatible(customer_name, order_by_id.customer_name)

        if name_conflict and not phone_matches:
            candidates = [
                _format_candidate(
                    order_by_id,
                    62,
                    [
                        f"Message mentions Order {order_by_id.order_number_raw}",
                        f"Sender name '{customer_name}' does not match order customer '{order_by_id.customer_name}'",
                    ],
                    "complaint_text_order_unverified",
                )
            ]
            return {
                "status": MATCH_REVIEW,
                "matched_order": None,
                "method": "complaint_text_order_unverified",
                "score": 62,
                "explanation": (
                    f"Complaint text mentions Order {order_by_id.order_number_raw}, "
                    f"but sender name '{customer_name}' differs from '{order_by_id.customer_name}' and phone is not confirmed."
                ),
                "candidates": candidates,
            }

        if phone_matches or name_compat:
            evidence_parts = [f"Complaint text mentions Order {order_by_id.order_number_raw}"]
            if phone_matches:
                evidence_parts.append(f"validated by matching phone ({order_by_id.customer_phone_masked})")
            if name_compat:
                evidence_parts.append(f"validated by customer name '{order_by_id.customer_name}'")
            return {
                "status": MATCH_STRONG,
                "matched_order": order_by_id,
                "method": "complaint_text_order_validated",
                "score": 90,
                "explanation": ", ".join(evidence_parts) + ".",
                "candidates": [_format_candidate(order_by_id, 90, evidence_parts, "complaint_text_order_validated")],
            }

        # Mentioned in text, but no phone or name corroboration -> Needs review
        reasons = [
            f"Message mentions Order {order_by_id.order_number_raw}, but lacks corroborating phone or customer name"
        ]
        return {
            "status": MATCH_REVIEW,
            "matched_order": None,
            "method": "complaint_text_order_unverified",
            "score": 70,
            "explanation": (
                f"Complaint message mentions Order {order_by_id.order_number_raw}, "
                "but missing phone/name evidence requires manual confirmation."
            ),
            "candidates": [_format_candidate(order_by_id, 70, reasons, "complaint_text_order_unverified")],
        }

    # -------------------------------------------------------------------------
    # 5. Level 3b: Normalized Phone Number + Supporting Evidence
    # -------------------------------------------------------------------------
    if phone_norm and len(phone_orders) > 1:
        # Shared household phone across multiple orders -> Always require manual review!
        candidates = []
        for ord_cand in phone_orders:
            c_reasons = [f"Shares normalized phone number {ord_cand.customer_phone_masked}"]
            c_score = 68
            name_ok, _ = _names_compatible(customer_name, ord_cand.customer_name)
            if name_ok:
                c_reasons.append(f"Customer name matches '{ord_cand.customer_name}'")
                c_score += 6
            if amount_paise and abs(amount_paise - ord_cand.total_amount_paise) <= 100:
                c_reasons.append(f"Order amount matches {format_paise_to_inr(ord_cand.total_amount_paise)}")
                c_score += 6
            candidates.append(_format_candidate(ord_cand, c_score, c_reasons, "shared_phone_multiple_orders"))

        candidates.sort(key=lambda c: c["heuristic_score"], reverse=True)
        order_nums = ", ".join(o.order_number_raw for o in phone_orders)
        return {
            "status": MATCH_REVIEW,
            "matched_order": None,
            "method": "shared_phone_multiple_orders",
            "score": candidates[0]["heuristic_score"],
            "explanation": (
                f"Phone {mask_phone(phone_norm)} is shared by {len(phone_orders)} orders ({order_nums}). "
                "Manual review is required to attach this record to the right order."
            ),
            "candidates": candidates,
        }

    if phone_norm and len(phone_orders) == 1:
        ord_single = phone_orders[0]
        supporting: list[str] = []
        conflicts: list[str] = []

        name_ok, name_conflict = _names_compatible(customer_name, ord_single.customer_name)
        if name_ok:
            supporting.append(f"matching customer name ('{ord_single.customer_name}')")
        elif name_conflict:
            conflicts.append(
                f"record name '{customer_name}' differs from order customer '{ord_single.customer_name}'"
            )

        if amount_paise is not None and amount_paise > 0:
            if abs(amount_paise - ord_single.total_amount_paise) <= 100:
                supporting.append(f"matching order value ({format_paise_to_inr(ord_single.total_amount_paise)})")
            else:
                conflicts.append(
                    f"record amount ({format_paise_to_inr(amount_paise)}) differs from order value "
                    f"({format_paise_to_inr(ord_single.total_amount_paise)})"
                )

        if location_or_city and ord_single.city:
            if ord_single.city.lower() in location_or_city.lower() or location_or_city.lower() in ord_single.city.lower():
                supporting.append(f"compatible city/hub ('{ord_single.city}')")

        date_plausible = True
        if event_dt and ord_single.order_date:
            ord_dt = _ensure_utc(ord_single.order_date)
            if ord_dt:
                if event_dt < (ord_dt - timedelta(hours=12)):
                    date_plausible = False
                    conflicts.append(
                        f"record timestamp ({format_ist(event_dt, False)}) is earlier than order date ({format_ist(ord_dt, False)})"
                    )
                elif (event_dt - ord_dt).days <= 30:
                    supporting.append(f"plausible date window after order date ({format_ist(ord_dt, False)})")

        if conflicts:
            reasons = [f"Phone matches {ord_single.customer_phone_masked}"] + [f"Conflict: {c}" for c in conflicts]
            return {
                "status": MATCH_REVIEW,
                "matched_order": None,
                "method": "phone_match_conflicting_details",
                "score": 62,
                "explanation": (
                    f"Phone {ord_single.customer_phone_masked} matches Order {ord_single.order_number_raw}, "
                    f"but {', '.join(conflicts)}. Routed for manual review."
                ),
                "candidates": [_format_candidate(ord_single, 62, reasons, "phone_match_conflicting_details")],
            }

        if supporting and date_plausible:
            reasons = [f"Exact normalized phone match ({ord_single.customer_phone_masked})"] + [
                f"Corroborated by {s}" for s in supporting
            ]
            return {
                "status": MATCH_STRONG,
                "matched_order": ord_single,
                "method": "phone_plus_supporting_evidence",
                "score": 85,
                "explanation": (
                    f"Strong match to Order {ord_single.order_number_raw} via phone {ord_single.customer_phone_masked} "
                    f"plus {', '.join(supporting)}."
                ),
                "candidates": [_format_candidate(ord_single, 85, reasons, "phone_plus_supporting_evidence")],
            }

        # Phone matched one order, but zero supporting evidence fields were present
        reasons = [
            f"Phone matches {ord_single.customer_phone_masked}, but record has no corroborating name, amount, or date window"
        ]
        return {
            "status": MATCH_REVIEW,
            "matched_order": None,
            "method": "phone_only_needs_confirmation",
            "score": 66,
            "explanation": (
                f"Phone {ord_single.customer_phone_masked} matches Order {ord_single.order_number_raw}, "
                "but missing supporting evidence requires manual review."
            ),
            "candidates": [_format_candidate(ord_single, 66, reasons, "phone_only_needs_confirmation")],
        }

    # -------------------------------------------------------------------------
    # 6. Level 4 & 5: Multiple Compatible Fields / Fuzzy Candidate Suggestions
    #    Hard rule: Never auto-match on customer name alone or order amount alone!
    # -------------------------------------------------------------------------
    name_Matched_orders: list[tuple[Order, list[str], int]] = []
    if customer_name:
        for ord_cand in all_orders:
            name_ok, _ = _names_compatible(customer_name, ord_cand.customer_name)
            if not name_ok:
                continue
            c_reasons = [f"Customer name compatible with '{ord_cand.customer_name}'"]
            c_score = 48
            if amount_paise and abs(amount_paise - ord_cand.total_amount_paise) <= 100:
                c_reasons.append(f"Order value matches {format_paise_to_inr(ord_cand.total_amount_paise)}")
                c_score += 18
            if location_or_city and ord_cand.city and (
                ord_cand.city.lower() in location_or_city.lower() or location_or_city.lower() in ord_cand.city.lower()
            ):
                c_reasons.append(f"City/location matches '{ord_cand.city}'")
                c_score += 10
            if event_dt and ord_cand.order_date:
                ord_dt = _ensure_utc(ord_cand.order_date)
                if ord_dt and timedelta(hours=-6) <= (event_dt - ord_dt) <= timedelta(days=21):
                    c_reasons.append(f"Timestamp aligns with order date ({format_ist(ord_dt, False)})")
                    c_score += 8
            name_Matched_orders.append((ord_cand, c_reasons, min(c_score, 78)))

    if name_Matched_orders:
        name_Matched_orders.sort(key=lambda item: item[2], reverse=True)
        candidates = [
            _format_candidate(o, sc, r, "compatible_fields_suggestion")
            for o, r, sc in name_Matched_orders[:4]
        ]
        top_score = candidates[0]["heuristic_score"]
        if len(name_Matched_orders) > 1:
            explanation = (
                f"Customer name '{customer_name}' matches {len(name_Matched_orders)} orders "
                f"({', '.join(o.order_number_raw for o, _, _ in name_Matched_orders[:3])}). "
                "Records are never auto-matched by customer name; please review candidates."
            )
        else:
            ord_top = name_Matched_orders[0][0]
            explanation = (
                f"Suggested candidate Order {ord_top.order_number_raw} based on compatible fields "
                f"({', '.join(name_Matched_orders[0][1])}). Manual confirmation required because strong identifiers (Order ID, AWB, Phone) are missing."
            )
        return {
            "status": MATCH_REVIEW,
            "matched_order": None,
            "method": "compatible_fields_suggestion",
            "score": top_score,
            "explanation": explanation,
            "candidates": candidates,
        }

    # No confident match or candidate
    return {
        "status": MATCH_UNMATCHED,
        "matched_order": None,
        "method": "unmatched",
        "score": 0,
        "explanation": "No matching Order ID, AWB, phone number, or compatible customer record found.",
        "candidates": [],
    }


def recompute_shipment_state(db: Session, shipment: Shipment) -> None:
    """Recompute a Shipment's latest status and conflict flags from its chronological ShipmentEvents.

    Rules:
    - Events are ordered chronologically by `event_at ASC, id ASC`.
    - Late-arriving older events do NOT overwrite a newer shipment status.
    - If an active transit/attempt scan occurs AFTER a terminal 'delivered' or 'rto_delivered' scan,
      flag `has_conflicting_status = True` with a plain-English explanation.
    """
    events = list(
        db.scalars(
            select(ShipmentEvent)
            .where(ShipmentEvent.shipment_id == shipment.id)
            .order_by(ShipmentEvent.event_at.asc(), ShipmentEvent.id.asc())
        ).all()
    )
    if not events:
        return

    latest_ev = events[-1]
    shipment.current_status_raw = latest_ev.status_raw
    shipment.current_status_norm = latest_ev.status_norm
    shipment.latest_event_at = _ensure_utc(latest_ev.event_at)
    if latest_ev.location:
        shipment.latest_location = latest_ev.location

    # Count failed delivery attempts across events
    failed_events = [e for e in events if e.status_norm == "failed_attempt"]
    max_reported_attempts = max((e.delivery_attempts for e in events), default=0)
    shipment.failed_attempts_count = max(len(failed_events), max_reported_attempts)

    # Detect contradictory event sequence
    conflict_found = False
    conflict_notes: list[str] = []
    seen_delivered_ev: ShipmentEvent | None = None
    seen_rto_ev: ShipmentEvent | None = None

    for ev in events:
        if ev.status_norm == "delivered" and seen_delivered_ev is None:
            seen_delivered_ev = ev
        elif ev.status_norm in ("rto_initiated", "rto_delivered") and seen_rto_ev is None:
            seen_rto_ev = ev

        if seen_delivered_ev and ev.id != seen_delivered_ev.id:
            if ev.status_norm in ("in_transit", "out_for_delivery", "failed_attempt", "rto_initiated", "rto_delivered"):
                conflict_found = True
                conflict_notes.append(
                    f"Scan '{ev.status_raw}' ({format_ist(ev.event_at)}) occurred after 'Delivered' scan ({format_ist(seen_delivered_ev.event_at)})."
                )
        if seen_rto_ev and ev.id != seen_rto_ev.id and ev.status_norm == "delivered":
            conflict_found = True
            conflict_notes.append(
                f"Scan '{ev.status_raw}' ({format_ist(ev.event_at)}) contradicts earlier RTO scan '{seen_rto_ev.status_raw}' ({format_ist(seen_rto_ev.event_at)})."
            )

    shipment.has_conflicting_status = conflict_found
    shipment.conflict_explanation = " ".join(conflict_notes)


def reconcile_workspace(db: Session, workspace_id: str) -> dict[str, int]:
    """Run deterministic reconciliation across all Orders, Shipments, Complaints, and RawSourceRecords."""
    from src.attention_rules import evaluate_workspace_attention_flags

    all_orders = list(
        db.scalars(select(Order).where(Order.workspace_id == workspace_id)).all()
    )
    orders_by_norm_id: dict[str, Order] = {o.order_number_norm: o for o in all_orders}
    orders_by_phone: dict[str, list[Order]] = {}
    for o in all_orders:
        if o.customer_phone_norm:
            orders_by_phone.setdefault(o.customer_phone_norm, []).append(o)

    # Build initial tracking map from Shopify line items & already exact-matched shipments
    orders_by_tracking: dict[str, Order] = {}
    line_items = list(
        db.scalars(select(OrderLineItem).where(OrderLineItem.workspace_id == workspace_id)).all()
    )
    orders_by_pk = {o.id: o for o in all_orders}
    for li in line_items:
        if li.tracking_number_raw:
            from src.normalization import normalize_tracking_number

            tn = normalize_tracking_number(li.tracking_number_raw)
            if tn and li.order_id in orders_by_pk:
                orders_by_tracking[tn] = orders_by_pk[li.order_id]

    # Map manual decisions by raw_record_id (latest decision wins)
    decisions = list(
        db.scalars(
            select(MatchDecision)
            .where(MatchDecision.workspace_id == workspace_id)
            .order_by(MatchDecision.decided_at.asc(), MatchDecision.id.asc())
        ).all()
    )
    manual_by_raw_id: dict[int, MatchDecision] = {}
    for dec in decisions:
        manual_by_raw_id[dec.raw_record_id] = dec

    raw_records = list(
        db.scalars(
            select(RawSourceRecord)
            .where(
                RawSourceRecord.workspace_id == workspace_id,
                RawSourceRecord.validation_status == "accepted",
            )
            .order_by(RawSourceRecord.id.asc())
        ).all()
    )
    raw_by_id = {r.id: r for r in raw_records}

    # 1. Reconcile Shipments
    shipments = list(
        db.scalars(select(Shipment).where(Shipment.workspace_id == workspace_id)).all()
    )
    for shp in shipments:
        recompute_shipment_state(db, shp)

        raw_rec = raw_by_id.get(shp.raw_record_id) if shp.raw_record_id else None
        manual_dec = manual_by_raw_id.get(shp.raw_record_id) if shp.raw_record_id else None

        if manual_dec:
            if manual_dec.decision_type in ("confirm_match", "change_match") and manual_dec.selected_order_id:
                matched_ord = orders_by_pk.get(manual_dec.selected_order_id)
                shp.order_id = matched_ord.id if matched_ord else None
                shp.match_status = MATCH_MANUAL
                shp.match_method = "manual_confirmation"
                shp.match_score = 100
                shp.match_explanation = (
                    f"Manually confirmed match to Order {matched_ord.order_number_raw if matched_ord else ''} "
                    f"({manual_dec.note or 'Confirmed in Match Review'})."
                )
            else:
                shp.order_id = None
                shp.match_status = MATCH_UNMATCHED
                shp.match_method = "manual_unmatched"
                shp.match_score = 0
                shp.match_explanation = manual_dec.note or "Manually marked as unmatched in Match Review."
        else:
            norm_payload = json.loads(raw_rec.normalized_payload_json) if raw_rec else {
                "tracking_number_norm": shp.tracking_number_norm,
                "order_number_ref_norm": shp.order_number_ref_norm,
                "customer_phone_norm": shp.customer_phone_norm,
                "location": shp.latest_location,
            }
            res = evaluate_order_match(
                source_type="courier_tracking",
                norm_payload=norm_payload,
                orders_by_norm_id=orders_by_norm_id,
                orders_by_tracking=orders_by_tracking,
                orders_by_phone=orders_by_phone,
                all_orders=all_orders,
            )
            matched_ord = res["matched_order"]
            shp.order_id = matched_ord.id if matched_ord else None
            shp.match_status = res["status"]
            shp.match_method = res["method"]
            shp.match_score = res["score"]
            shp.match_explanation = res["explanation"]
            if raw_rec:
                raw_rec.candidate_matches_json = json.dumps(res["candidates"])

        if shp.order_id and shp.order_id in orders_by_pk:
            orders_by_tracking[shp.tracking_number_norm] = orders_by_pk[shp.order_id]

    # Update all courier raw records to mirror their shipment's reconciliation state
    shipments_by_tracking = {s.tracking_number_norm: s for s in shipments}
    for r in raw_records:
        if r.source_type == "shopify_orders":
            norm_p = json.loads(r.normalized_payload_json)
            ord_obj = orders_by_norm_id.get(norm_p.get("order_number_norm", ""))
            r.reconciliation_status = MATCH_EXACT
            r.matched_order_id = ord_obj.id if ord_obj else None
            r.match_method = "shopify_source"
            r.match_score = 100
            r.match_explanation = "Primary Shopify order record."
        elif r.source_type == "courier_tracking":
            norm_p = json.loads(r.normalized_payload_json)
            shp_obj = shipments_by_tracking.get(norm_p.get("tracking_number_norm", ""))
            manual_dec = manual_by_raw_id.get(r.id)
            if manual_dec and shp_obj:
                if manual_dec.decision_type in ("confirm_match", "change_match") and manual_dec.selected_order_id:
                    matched_ord = orders_by_pk.get(manual_dec.selected_order_id)
                    shp_obj.order_id = matched_ord.id if matched_ord else None
                    shp_obj.match_status = MATCH_MANUAL
                    shp_obj.match_method = "manual_confirmation"
                    shp_obj.match_score = 100
                    shp_obj.match_explanation = (
                        f"Manually confirmed match to Order {matched_ord.order_number_raw if matched_ord else ''}."
                    )
                else:
                    shp_obj.order_id = None
                    shp_obj.match_status = MATCH_UNMATCHED
                    shp_obj.match_method = "manual_unmatched"
                    shp_obj.match_score = 0
                    shp_obj.match_explanation = manual_dec.note or "Manually left unmatched."
            if shp_obj:
                r.reconciliation_status = shp_obj.match_status
                r.matched_order_id = shp_obj.order_id
                r.match_method = shp_obj.match_method
                r.match_score = shp_obj.match_score
                r.match_explanation = shp_obj.match_explanation

    # 2. Reconcile Complaints
    complaints = list(
        db.scalars(select(Complaint).where(Complaint.workspace_id == workspace_id)).all()
    )
    for comp in complaints:
        raw_rec = raw_by_id.get(comp.raw_record_id) if comp.raw_record_id else None
        manual_dec = manual_by_raw_id.get(comp.raw_record_id) if comp.raw_record_id else None

        if manual_dec:
            if manual_dec.decision_type in ("confirm_match", "change_match") and manual_dec.selected_order_id:
                matched_ord = orders_by_pk.get(manual_dec.selected_order_id)
                comp.order_id = matched_ord.id if matched_ord else None
                comp.match_status = MATCH_MANUAL
                comp.match_method = "manual_confirmation"
                comp.match_score = 100
                comp.match_explanation = (
                    f"Manually confirmed match to Order {matched_ord.order_number_raw if matched_ord else ''} "
                    f"({manual_dec.note or 'Confirmed in Match Review'})."
                )
            else:
                comp.order_id = None
                comp.match_status = MATCH_UNMATCHED
                comp.match_method = "manual_unmatched"
                comp.match_score = 0
                comp.match_explanation = manual_dec.note or "Manually marked as unmatched in Match Review."
        else:
            norm_payload = json.loads(raw_rec.normalized_payload_json) if raw_rec else {
                "customer_phone_norm": comp.customer_phone_norm,
                "customer_name": comp.customer_name,
                "order_number_mentioned_norm": comp.order_number_mentioned_norm,
                "tracking_mentioned_norm": comp.tracking_mentioned_norm,
                "complaint_timestamp_utc": comp.complaint_at.isoformat() if comp.complaint_at else None,
            }
            res = evaluate_order_match(
                source_type="whatsapp_complaints",
                norm_payload=norm_payload,
                orders_by_norm_id=orders_by_norm_id,
                orders_by_tracking=orders_by_tracking,
                orders_by_phone=orders_by_phone,
                all_orders=all_orders,
            )
            matched_ord = res["matched_order"]
            comp.order_id = matched_ord.id if matched_ord else None
            comp.match_status = res["status"]
            comp.match_method = res["method"]
            comp.match_score = res["score"]
            comp.match_explanation = res["explanation"]
            if raw_rec:
                raw_rec.candidate_matches_json = json.dumps(res["candidates"])

        if raw_rec:
            raw_rec.reconciliation_status = comp.match_status
            raw_rec.matched_order_id = comp.order_id
            raw_rec.match_method = comp.match_method
            raw_rec.match_score = comp.match_score
            raw_rec.match_explanation = comp.match_explanation

    db.flush()

    # 3. Update Order-level match_status summary and evaluate operational attention flags
    # Also check if any Needs Review raw record mentions this order as a candidate
    candidate_order_ids_needing_review: set[int] = set()
    for r in raw_records:
        if r.reconciliation_status == MATCH_REVIEW:
            try:
                cands = json.loads(r.candidate_matches_json or "[]")
                for c in cands:
                    if c.get("order_id"):
                        candidate_order_ids_needing_review.add(int(c["order_id"]))
            except Exception:
                pass

    for ord_obj in all_orders:
        linked_shipments = [s for s in shipments if s.order_id == ord_obj.id]
        linked_complaints = [c for c in complaints if c.order_id == ord_obj.id]
        linked_statuses = [s.match_status for s in linked_shipments] + [c.match_status for c in linked_complaints]
        explanations = [s.match_explanation for s in linked_shipments if s.match_explanation] + [
            c.match_explanation for c in linked_complaints if c.match_explanation
        ]

        if ord_obj.id in candidate_order_ids_needing_review:
            ord_obj.match_status = MATCH_REVIEW
            ord_obj.match_explanation = (
                "One or more courier or complaint records have an ambiguous or conflicting match referencing this order."
            )
        elif MATCH_MANUAL in linked_statuses:
            ord_obj.match_status = MATCH_MANUAL
            ord_obj.match_explanation = " | ".join(explanations[:2])
        elif MATCH_STRONG in linked_statuses:
            ord_obj.match_status = MATCH_STRONG
            ord_obj.match_explanation = " | ".join(explanations[:2])
        elif linked_statuses:
            ord_obj.match_status = MATCH_EXACT
            ord_obj.match_explanation = " | ".join(explanations[:2])
        else:
            ord_obj.match_status = MATCH_EXACT
            ord_obj.match_explanation = "Imported from Shopify order export; no external courier/complaint records linked yet."

    db.flush()
    flag_counts = evaluate_workspace_attention_flags(db, workspace_id)
    db.commit()

    review_count = sum(1 for r in raw_records if r.reconciliation_status == MATCH_REVIEW and r.source_type != "shopify_orders")
    unmatched_count = sum(1 for r in raw_records if r.reconciliation_status == MATCH_UNMATCHED and r.source_type != "shopify_orders")

    return {
        "orders_total": len(all_orders),
        "shipments_total": len(shipments),
        "complaints_total": len(complaints),
        "needs_review_records": review_count,
        "unmatched_records": unmatched_count,
        "flagged_orders": flag_counts["flagged_orders"],
    }


def apply_manual_match_decision(
    db: Session,
    *,
    workspace_id: str,
    raw_record_id: int,
    decision_type: str,
    selected_order_id: int | None = None,
    note: str = "",
    decided_by: str = "Operations User",
) -> dict[str, Any]:
    """Record a manual match decision in the audit trail without modifying the raw imported payload."""
    raw_rec = db.scalar(
        select(RawSourceRecord).where(
            RawSourceRecord.id == raw_record_id,
            RawSourceRecord.workspace_id == workspace_id,
        )
    )
    if not raw_rec:
        raise ValueError(f"Source record #{raw_record_id} not found in workspace.")

    if decision_type not in ("confirm_match", "reject_candidate", "leave_unmatched", "change_match"):
        raise ValueError(f"Unsupported decision_type '{decision_type}'.")

    target_order: Order | None = None
    if decision_type in ("confirm_match", "change_match"):
        if not selected_order_id:
            raise ValueError("selected_order_id is required when confirming or changing a match.")
        target_order = db.scalar(
            select(Order).where(
                Order.id == selected_order_id,
                Order.workspace_id == workspace_id,
            )
        )
        if not target_order:
            raise ValueError(f"Selected order #{selected_order_id} not found.")
        new_status = MATCH_MANUAL
    else:
        selected_order_id = None
        new_status = MATCH_UNMATCHED

    decision = MatchDecision(
        workspace_id=workspace_id,
        raw_record_id=raw_rec.id,
        source_type=raw_rec.source_type,
        previous_order_id=raw_rec.matched_order_id,
        selected_order_id=selected_order_id,
        decision_type=decision_type,
        previous_status=raw_rec.reconciliation_status,
        new_status=new_status,
        note=note.strip() or (
            f"Matched to Order {target_order.order_number_raw}" if target_order else "Marked as unmatched"
        ),
        decided_by=decided_by,
    )
    db.add(decision)
    db.flush()

    summary = reconcile_workspace(db, workspace_id)
    return {
        "decision_id": decision.id,
        "raw_record_id": raw_rec.id,
        "new_status": new_status,
        "selected_order_id": selected_order_id,
        "selected_order_number": target_order.order_number_raw if target_order else None,
        "reconciliation_summary": summary,
    }
