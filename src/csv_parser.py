"""CSV parsing, encoding handling, column mapping suggestion, and row-level validation."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from src.normalization import (
    detect_claims_not_received,
    extract_order_id_from_text,
    extract_tracking_from_text,
    mask_phone,
    normalize_fulfillment_status,
    normalize_order_id,
    normalize_payment_method,
    normalize_phone,
    normalize_shipment_status,
    normalize_tracking_number,
    parse_inr_to_paise,
    parse_timestamp_ist,
    sanitize_csv_cell,
    to_ist_iso,
)

SOURCE_TYPES = ("shopify_orders", "courier_tracking", "whatsapp_complaints")

# Canonical fields and their metadata per source type
FIELD_DEFINITIONS: dict[str, dict[str, dict[str, Any]]] = {
    "shopify_orders": {
        "order_id": {
            "label": "Order ID / Order Number",
            "required": True,
            "aliases": [
                "order id",
                "order number",
                "name",
                "order #",
                "order_id",
                "order_number",
                "order no",
                "shopify order",
            ],
        },
        "order_date": {
            "label": "Order Date",
            "required": True,
            "aliases": [
                "order date",
                "created at",
                "created_at",
                "date",
                "order timestamp",
                "placed at",
            ],
        },
        "total_amount": {
            "label": "Order Total Amount (INR)",
            "required": True,
            "aliases": [
                "total",
                "order value",
                "total amount",
                "amount",
                "grand total",
                "order total",
                "total_amount",
                "subtotal",
            ],
        },
        "customer_name": {
            "label": "Customer Name",
            "required": False,
            "aliases": [
                "customer name",
                "shipping name",
                "billing name",
                "customer",
                "recipient name",
                "buyer name",
            ],
        },
        "customer_phone": {
            "label": "Customer Phone",
            "required": False,
            "aliases": [
                "phone",
                "mobile",
                "customer phone",
                "customer mobile",
                "shipping phone",
                "billing phone",
                "contact number",
                "mobile no",
                "mobile number",
                "phone number",
            ],
        },
        "customer_email": {
            "label": "Customer Email",
            "required": False,
            "aliases": ["email", "customer email", "contact email"],
        },
        "city": {
            "label": "Shipping City",
            "required": False,
            "aliases": ["city", "shipping city", "destination city", "town", "billing city"],
        },
        "state": {
            "label": "State / Province",
            "required": False,
            "aliases": ["state", "shipping province", "province", "shipping state"],
        },
        "pincode": {
            "label": "Pincode / Postal Code",
            "required": False,
            "aliases": ["pincode", "zip", "shipping zip", "postal code", "pin", "pin code"],
        },
        "payment_method": {
            "label": "Payment Method (COD / Prepaid)",
            "required": False,
            "aliases": [
                "payment method",
                "gateway",
                "payment mode",
                "payment type",
                "payment_method",
            ],
        },
        "financial_status": {
            "label": "Financial Status",
            "required": False,
            "aliases": ["financial status", "payment status", "financial_status"],
        },
        "fulfillment_status": {
            "label": "Fulfillment Status",
            "required": False,
            "aliases": ["fulfillment status", "order status", "status", "fulfillment_status"],
        },
        "cancelled_at": {
            "label": "Cancelled At / Cancelled Flag",
            "required": False,
            "aliases": ["cancelled at", "cancelled", "is cancelled", "cancelled_at", "canceled at"],
        },
        "sku": {
            "label": "Line Item SKU",
            "required": False,
            "aliases": ["lineitem sku", "sku", "item sku", "product sku"],
        },
        "product_title": {
            "label": "Product / Line Item Name",
            "required": False,
            "aliases": ["lineitem name", "product title", "item name", "product", "product name"],
        },
        "quantity": {
            "label": "Line Item Quantity",
            "required": False,
            "aliases": ["lineitem quantity", "quantity", "qty", "item qty"],
        },
        "unit_price": {
            "label": "Line Item Price",
            "required": False,
            "aliases": ["lineitem price", "unit price", "item price", "price"],
        },
        "tracking_number": {
            "label": "Tracking Number / AWB (Optional)",
            "required": False,
            "aliases": ["tracking number", "awb", "waybill", "fulfillment tracking", "tracking_number"],
        },
        "courier_name": {
            "label": "Courier / Shipping Partner (Optional)",
            "required": False,
            "aliases": ["courier", "tracking company", "carrier", "shipping partner", "courier name"],
        },
        "promised_delivery_date": {
            "label": "Promised Delivery Date (Optional)",
            "required": False,
            "aliases": ["promised delivery date", "expected delivery", "edd", "promised date"],
        },
    },
    "courier_tracking": {
        "tracking_number": {
            "label": "Tracking Number / AWB / Waybill",
            "required": True,
            "aliases": [
                "tracking number",
                "awb",
                "waybill",
                "awb number",
                "awb no",
                "consignment no",
                "docket no",
                "tracking_number",
            ],
        },
        "status": {
            "label": "Shipment / Scan Status",
            "required": True,
            "aliases": [
                "status",
                "shipment status",
                "current status",
                "scan status",
                "tracking status",
                "delivery status",
            ],
        },
        "event_timestamp": {
            "label": "Scan / Status Update Timestamp",
            "required": True,
            "aliases": [
                "event time",
                "last update",
                "updated at",
                "scan timestamp",
                "status date",
                "timestamp",
                "date",
                "event_timestamp",
                "scan time",
            ],
        },
        "courier_name": {
            "label": "Courier Name",
            "required": False,
            "aliases": [
                "courier",
                "courier name",
                "carrier",
                "logistics partner",
                "delivery partner",
                "shipping partner",
            ],
        },
        "order_id": {
            "label": "Order Reference / Order ID",
            "required": False,
            "aliases": [
                "order id",
                "order number",
                "reference no",
                "client order id",
                "channel order id",
                "order ref",
                "name",
                "ref no",
            ],
        },
        "customer_phone": {
            "label": "Customer / Consignee Phone",
            "required": False,
            "aliases": [
                "phone",
                "mobile",
                "customer phone",
                "consignee phone",
                "recipient mobile",
                "contact number",
            ],
        },
        "customer_name": {
            "label": "Customer / Consignee Name",
            "required": False,
            "aliases": ["customer name", "consignee name", "recipient", "consignee", "name"],
        },
        "location": {
            "label": "Scan Location / Hub",
            "required": False,
            "aliases": ["location", "scan location", "hub", "current hub", "city", "destination"],
        },
        "cod_amount": {
            "label": "Order / COD Amount",
            "required": False,
            "aliases": ["cod amount", "collectable value", "order amount", "amount", "declared value"],
        },
        "expected_delivery_date": {
            "label": "Expected Delivery Date (EDD)",
            "required": False,
            "aliases": ["expected delivery date", "edd", "promised date", "expected delivery"],
        },
        "delivery_attempts": {
            "label": "Delivery Attempts Count",
            "required": False,
            "aliases": ["delivery attempts", "attempt count", "attempts", "ndr count", "failed attempts"],
        },
        "remarks": {
            "label": "Courier Remarks / NDR Reason",
            "required": False,
            "aliases": ["remarks", "ndr reason", "reason", "comments", "status remarks", "notes"],
        },
    },
    "whatsapp_complaints": {
        "complaint_timestamp": {
            "label": "Message / Complaint Timestamp",
            "required": True,
            "aliases": [
                "timestamp",
                "date",
                "received at",
                "message time",
                "created at",
                "complaint date",
                "time",
            ],
        },
        "message_text": {
            "label": "Complaint Message Text",
            "required": True,
            "aliases": [
                "message",
                "complaint text",
                "message body",
                "customer message",
                "issue description",
                "text",
                "whatsapp message",
            ],
        },
        "customer_phone": {
            "label": "Customer WhatsApp / Phone Number",
            "required": False,
            "aliases": [
                "phone",
                "mobile",
                "customer phone",
                "whatsapp number",
                "sender phone",
                "from",
                "contact number",
            ],
        },
        "customer_name": {
            "label": "Customer / Sender Name",
            "required": False,
            "aliases": ["customer name", "sender name", "profile name", "name", "customer"],
        },
        "order_id": {
            "label": "Order ID (if column exists)",
            "required": False,
            "aliases": ["order id", "order number", "mentioned order", "order ref", "order_id"],
        },
        "tracking_number": {
            "label": "Tracking Number / AWB (if column exists)",
            "required": False,
            "aliases": ["tracking number", "awb", "waybill", "tracking_number"],
        },
        "complaint_ref": {
            "label": "Complaint / Ticket ID",
            "required": False,
            "aliases": ["complaint id", "ticket id", "reference", "msg id", "id", "complaint_ref"],
        },
        "issue_category": {
            "label": "Issue Category",
            "required": False,
            "aliases": ["category", "issue type", "topic", "tag", "issue category"],
        },
        "status": {
            "label": "Complaint Status (Open / Resolved)",
            "required": False,
            "aliases": ["status", "complaint status", "ticket status", "resolution status"],
        },
    },
}


def decode_csv_bytes(raw_bytes: bytes) -> tuple[str, str]:
    """Decode CSV bytes safely across UTF-8-SIG, UTF-8, CP1252, and Latin-1."""
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = raw_bytes.decode(encoding)
            return text, encoding
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="replace"), "utf-8-replace"


def _clean_header(header: str) -> str:
    cleaned = re.sub(r"[\s_\-]+", " ", str(header or "").strip().lower())
    return cleaned


def suggest_column_mapping(source_type: str, csv_headers: list[str]) -> dict[str, str]:
    """Suggest a mapping of {canonical_field: csv_column_header} for a source type."""
    if source_type not in FIELD_DEFINITIONS:
        raise ValueError(f"Unsupported source_type '{source_type}'")

    defs = FIELD_DEFINITIONS[source_type]
    cleaned_headers = {_clean_header(h): h for h in csv_headers if h and str(h).strip()}
    used_headers: set[str] = set()
    mapping: dict[str, str] = {}

    # Pass 1: Exact alias match
    for canonical_field, meta in defs.items():
        for alias in meta["aliases"]:
            alias_clean = _clean_header(alias)
            if alias_clean in cleaned_headers:
                orig_header = cleaned_headers[alias_clean]
                if orig_header not in used_headers:
                    mapping[canonical_field] = orig_header
                    used_headers.add(orig_header)
                    break

    # Pass 2: Substring fallback for still-unmapped fields
    for canonical_field, meta in defs.items():
        if canonical_field in mapping:
            continue
        for alias in meta["aliases"]:
            alias_clean = _clean_header(alias)
            if len(alias_clean) < 4:
                continue
            for h_clean, orig_header in cleaned_headers.items():
                if orig_header in used_headers:
                    continue
                if alias_clean in h_clean or h_clean in alias_clean:
                    mapping[canonical_field] = orig_header
                    used_headers.add(orig_header)
                    break
            if canonical_field in mapping:
                break

    return mapping


def get_required_fields(source_type: str) -> list[dict[str, str]]:
    defs = FIELD_DEFINITIONS.get(source_type, {})
    return [
        {"field": k, "label": v["label"]}
        for k, v in defs.items()
        if v.get("required")
    ]


def get_all_field_options(source_type: str) -> list[dict[str, Any]]:
    defs = FIELD_DEFINITIONS.get(source_type, {})
    return [
        {"field": k, "label": v["label"], "required": bool(v.get("required"))}
        for k, v in defs.items()
    ]


@dataclass
class ParsedRowRecord:
    row_number: int
    raw_row: dict[str, str]
    normalized: dict[str, Any]
    row_fingerprint: str
    validation_status: str  # "accepted" | "rejected" | "duplicate"
    errors: list[str] = field(default_factory=list)
    has_ambiguous_date: bool = False


@dataclass
class CSVParseResult:
    source_type: str
    filename: str
    file_hash: str
    encoding_used: str
    headers: list[str]
    column_mapping: dict[str, str]
    missing_required_fields: list[dict[str, str]]
    date_format_preference: str
    ambiguous_date_samples: list[dict[str, Any]]
    total_rows: int
    accepted_rows: int
    rejected_rows: int
    duplicate_rows: int
    sample_rows: list[dict[str, Any]]
    parsed_rows: list[ParsedRowRecord]
    error_rows: list[dict[str, Any]]


def _extract_mapped_value(row: dict[str, str], mapping: dict[str, str], field_name: str) -> str:
    col = mapping.get(field_name)
    if not col:
        return ""
    val = row.get(col, "")
    if val is None:
        return ""
    return str(val).strip()


def compute_row_fingerprint(source_type: str, normalized: dict[str, Any], raw_row: dict[str, str]) -> str:
    """Compute a deterministic fingerprint so repeated imports are idempotent."""
    if source_type == "shopify_orders":
        key_parts = [
            source_type,
            normalized.get("order_number_norm", ""),
            normalized.get("sku", ""),
            normalized.get("product_title", ""),
            str(normalized.get("quantity", 1)),
            str(normalized.get("unit_price_paise", normalized.get("total_amount_paise", 0))),
            normalized.get("tracking_number_norm", ""),
        ]
    elif source_type == "courier_tracking":
        key_parts = [
            source_type,
            normalized.get("tracking_number_norm", ""),
            normalized.get("status_norm", ""),
            normalized.get("event_timestamp_iso", ""),
            normalized.get("location", "").lower(),
        ]
    elif source_type == "whatsapp_complaints":
        key_parts = [
            source_type,
            normalized.get("complaint_ref", ""),
            normalized.get("customer_phone_norm", "") or normalized.get("customer_phone_raw", ""),
            normalized.get("complaint_timestamp_iso", ""),
            normalized.get("message_text", "").strip().lower(),
        ]
    else:
        key_parts = [source_type, json.dumps(raw_row, sort_keys=True)]

    joined = "|".join(str(p) for p in key_parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:40]


def parse_and_validate_csv(
    raw_bytes: bytes,
    filename: str,
    source_type: str,
    custom_mapping: dict[str, str] | None = None,
    date_format_preference: str = "auto",
    existing_fingerprints: set[str] | None = None,
) -> CSVParseResult:
    """Parse a CSV file, apply column mappings, validate rows, and detect duplicates or ambiguous dates."""
    if source_type not in FIELD_DEFINITIONS:
        raise ValueError(f"Unsupported source_type '{source_type}'")

    file_hash = hashlib.sha256(raw_bytes).hexdigest()
    decoded_text, encoding_used = decode_csv_bytes(raw_bytes)

    stream = io.StringIO(decoded_text)
    reader = csv.reader(stream)
    try:
        raw_headers = next(reader)
    except StopIteration:
        raw_headers = []

    headers = [str(h).strip() for h in raw_headers if str(h).strip()]
    suggested = suggest_column_mapping(source_type, headers)
    mapping = dict(suggested)
    if custom_mapping:
        for k, v in custom_mapping.items():
            if v is None or v == "":
                mapping.pop(k, None)
            elif v in headers:
                mapping[k] = v

    # Check required fields
    missing_required: list[dict[str, str]] = []
    for req in get_required_fields(source_type):
        f_key = req["field"]
        if not mapping.get(f_key):
            missing_required.append(req)

    seen_fingerprints: set[str] = set(existing_fingerprints or set())
    parsed_rows: list[ParsedRowRecord] = []
    error_rows: list[dict[str, Any]] = []
    ambiguous_samples: list[dict[str, Any]] = []
    sample_rows: list[dict[str, Any]] = []

    accepted_count = 0
    rejected_count = 0
    duplicate_count = 0

    for idx, row_values in enumerate(reader):
        row_number = idx + 2  # 1-indexed spreadsheet row (header is row 1)
        if not row_values or all(not str(cell).strip() for cell in row_values):
            continue

        # Map header -> cell value, preserving extra columns under _extra if needed
        raw_row: dict[str, str] = {}
        for col_idx, header_name in enumerate(raw_headers):
            h_clean = str(header_name).strip()
            if not h_clean:
                continue
            val = row_values[col_idx] if col_idx < len(row_values) else ""
            raw_row[h_clean] = str(val).strip()

        if len(sample_rows) < 8:
            sample_rows.append({"row_number": row_number, "data": raw_row})

        errors: list[str] = []
        normalized: dict[str, Any] = {}
        has_ambiguous_date = False

        if missing_required:
            for req in missing_required:
                errors.append(f"Missing required column mapping for '{req['label']}'")

        elif source_type == "shopify_orders":
            order_id_raw = _extract_mapped_value(raw_row, mapping, "order_id")
            order_id_norm = normalize_order_id(order_id_raw)
            if not order_id_norm:
                errors.append("Missing required Order ID / Order Number")

            order_date_raw = _extract_mapped_value(raw_row, mapping, "order_date")
            ts_res = parse_timestamp_ist(order_date_raw, date_format_preference=date_format_preference)
            if ts_res.is_ambiguous:
                has_ambiguous_date = True
                if len(ambiguous_samples) < 5:
                    ambiguous_samples.append(
                        {"row_number": row_number, "field": "Order Date", "raw_value": order_date_raw}
                    )
                errors.append(ts_res.error_message or "Ambiguous Order Date")
            elif ts_res.error_message or ts_res.dt_utc is None:
                errors.append(f"Order Date error: {ts_res.error_message}")

            amount_raw = _extract_mapped_value(raw_row, mapping, "total_amount")
            amount_paise, amount_err = parse_inr_to_paise(amount_raw)
            if amount_err or amount_paise is None:
                errors.append(f"Order Total Amount error: {amount_err}")

            phone_raw = _extract_mapped_value(raw_row, mapping, "customer_phone")
            phone_norm, phone_valid = normalize_phone(phone_raw)

            promised_raw = _extract_mapped_value(raw_row, mapping, "promised_delivery_date")
            promised_utc = None
            if promised_raw:
                p_res = parse_timestamp_ist(promised_raw, date_format_preference=date_format_preference)
                if p_res.is_ambiguous:
                    has_ambiguous_date = True
                    errors.append(p_res.error_message or "Ambiguous Promised Delivery Date")
                elif p_res.dt_utc:
                    promised_utc = p_res.dt_utc

            cancelled_raw = _extract_mapped_value(raw_row, mapping, "cancelled_at")
            fulfillment_raw = _extract_mapped_value(raw_row, mapping, "fulfillment_status")
            financial_raw = _extract_mapped_value(raw_row, mapping, "financial_status") or "paid"
            is_cancelled = bool(
                cancelled_raw and cancelled_raw.lower() not in ("false", "0", "no", "none", "n/a")
            ) or (fulfillment_raw.strip().lower() in ("cancelled", "canceled")) or (
                financial_raw.strip().lower() in ("voided",)
            )

            qty_raw = _extract_mapped_value(raw_row, mapping, "quantity") or "1"
            try:
                quantity = max(1, int(Decimal(qty_raw)))
            except Exception:
                quantity = 1
                errors.append(f"Invalid line item quantity '{qty_raw}'")

            unit_price_raw = _extract_mapped_value(raw_row, mapping, "unit_price")
            if unit_price_raw:
                unit_price_paise, _ = parse_inr_to_paise(unit_price_raw)
            else:
                unit_price_paise = amount_paise or 0

            tracking_raw = _extract_mapped_value(raw_row, mapping, "tracking_number")
            tracking_norm = normalize_tracking_number(tracking_raw)

            normalized = {
                "order_number_raw": order_id_raw,
                "order_number_norm": order_id_norm,
                "order_date_utc": ts_res.dt_utc.isoformat() if ts_res.dt_utc else None,
                "order_date_ist": to_ist_iso(ts_res.dt_utc),
                "promised_delivery_utc": promised_utc.isoformat() if promised_utc else None,
                "customer_name": _extract_mapped_value(raw_row, mapping, "customer_name"),
                "customer_phone_raw": phone_raw,
                "customer_phone_norm": phone_norm if phone_valid else "",
                "customer_phone_masked": mask_phone(phone_norm if phone_valid else phone_raw),
                "customer_email": _extract_mapped_value(raw_row, mapping, "customer_email"),
                "city": _extract_mapped_value(raw_row, mapping, "city"),
                "state": _extract_mapped_value(raw_row, mapping, "state"),
                "pincode": _extract_mapped_value(raw_row, mapping, "pincode"),
                "total_amount_paise": amount_paise or 0,
                "payment_method": normalize_payment_method(
                    _extract_mapped_value(raw_row, mapping, "payment_method"),
                    financial_raw,
                ),
                "financial_status": financial_raw.strip().lower(),
                "fulfillment_status": normalize_fulfillment_status(fulfillment_raw, is_cancelled=is_cancelled),
                "is_cancelled": is_cancelled,
                "sku": _extract_mapped_value(raw_row, mapping, "sku"),
                "product_title": _extract_mapped_value(raw_row, mapping, "product_title") or "Standard Order Item",
                "quantity": quantity,
                "unit_price_paise": unit_price_paise or 0,
                "tracking_number_raw": tracking_raw,
                "tracking_number_norm": tracking_norm,
                "courier_name": _extract_mapped_value(raw_row, mapping, "courier_name"),
            }

        elif source_type == "courier_tracking":
            tracking_raw = _extract_mapped_value(raw_row, mapping, "tracking_number")
            tracking_norm = normalize_tracking_number(tracking_raw)
            if not tracking_norm:
                errors.append("Missing required Tracking Number / AWB")

            status_raw = _extract_mapped_value(raw_row, mapping, "status")
            if not status_raw:
                errors.append("Missing required Shipment Status")
            status_norm = normalize_shipment_status(status_raw)

            event_ts_raw = _extract_mapped_value(raw_row, mapping, "event_timestamp")
            ts_res = parse_timestamp_ist(event_ts_raw, date_format_preference=date_format_preference)
            if ts_res.is_ambiguous:
                has_ambiguous_date = True
                if len(ambiguous_samples) < 5:
                    ambiguous_samples.append(
                        {"row_number": row_number, "field": "Scan Timestamp", "raw_value": event_ts_raw}
                    )
                errors.append(ts_res.error_message or "Ambiguous Scan Timestamp")
            elif ts_res.error_message or ts_res.dt_utc is None:
                errors.append(f"Scan Timestamp error: {ts_res.error_message}")

            edd_raw = _extract_mapped_value(raw_row, mapping, "expected_delivery_date")
            edd_utc = None
            if edd_raw:
                edd_res = parse_timestamp_ist(edd_raw, date_format_preference=date_format_preference)
                if edd_res.is_ambiguous:
                    has_ambiguous_date = True
                    errors.append(edd_res.error_message or "Ambiguous Expected Delivery Date")
                elif edd_res.dt_utc:
                    edd_utc = edd_res.dt_utc

            order_ref_raw = _extract_mapped_value(raw_row, mapping, "order_id")
            order_ref_norm = normalize_order_id(order_ref_raw)

            phone_raw = _extract_mapped_value(raw_row, mapping, "customer_phone")
            phone_norm, phone_valid = normalize_phone(phone_raw)

            cod_raw = _extract_mapped_value(raw_row, mapping, "cod_amount")
            cod_paise = None
            if cod_raw:
                cod_paise, _ = parse_inr_to_paise(cod_raw)

            attempts_raw = _extract_mapped_value(raw_row, mapping, "delivery_attempts")
            try:
                attempts = max(0, int(Decimal(attempts_raw))) if attempts_raw else (1 if status_norm == "failed_attempt" else 0)
            except Exception:
                attempts = 1 if status_norm == "failed_attempt" else 0

            normalized = {
                "tracking_number_raw": tracking_raw,
                "tracking_number_norm": tracking_norm,
                "courier_name": _extract_mapped_value(raw_row, mapping, "courier_name") or "Standard Courier",
                "order_number_ref_raw": order_ref_raw,
                "order_number_ref_norm": order_ref_norm,
                "status_raw": status_raw,
                "status_norm": status_norm,
                "event_timestamp_utc": ts_res.dt_utc.isoformat() if ts_res.dt_utc else None,
                "event_timestamp_iso": ts_res.dt_utc.isoformat() if ts_res.dt_utc else "",
                "expected_delivery_utc": edd_utc.isoformat() if edd_utc else None,
                "location": _extract_mapped_value(raw_row, mapping, "location"),
                "customer_phone_raw": phone_raw,
                "customer_phone_norm": phone_norm if phone_valid else "",
                "customer_name": _extract_mapped_value(raw_row, mapping, "customer_name"),
                "cod_amount_paise": cod_paise,
                "delivery_attempts": attempts,
                "remarks": _extract_mapped_value(raw_row, mapping, "remarks"),
            }

        elif source_type == "whatsapp_complaints":
            ts_raw = _extract_mapped_value(raw_row, mapping, "complaint_timestamp")
            ts_res = parse_timestamp_ist(ts_raw, date_format_preference=date_format_preference)
            if ts_res.is_ambiguous:
                has_ambiguous_date = True
                if len(ambiguous_samples) < 5:
                    ambiguous_samples.append(
                        {"row_number": row_number, "field": "Complaint Timestamp", "raw_value": ts_raw}
                    )
                errors.append(ts_res.error_message or "Ambiguous Complaint Timestamp")
            elif ts_res.error_message or ts_res.dt_utc is None:
                errors.append(f"Complaint Timestamp error: {ts_res.error_message}")

            msg_text = _extract_mapped_value(raw_row, mapping, "message_text")
            if not msg_text:
                errors.append("Missing required Complaint Message Text")

            phone_raw = _extract_mapped_value(raw_row, mapping, "customer_phone")
            phone_norm, phone_valid = normalize_phone(phone_raw)

            col_order_raw = _extract_mapped_value(raw_row, mapping, "order_id")
            col_order_norm = normalize_order_id(col_order_raw)

            text_order_raw, text_order_norm = extract_order_id_from_text(msg_text)
            order_mentioned_raw = col_order_raw or text_order_raw
            order_mentioned_norm = col_order_norm or text_order_norm

            col_awb_raw = _extract_mapped_value(raw_row, mapping, "tracking_number")
            col_awb_norm = normalize_tracking_number(col_awb_raw)
            text_awb_norm = extract_tracking_from_text(msg_text)
            tracking_mentioned_norm = col_awb_norm or text_awb_norm

            customer_name = _extract_mapped_value(raw_row, mapping, "customer_name")
            if not phone_norm and not order_mentioned_norm and not tracking_mentioned_norm and not customer_name:
                errors.append(
                    "Complaint row has no identifiable phone number, order number, tracking number, or customer name."
                )

            category = _extract_mapped_value(raw_row, mapping, "issue_category") or "Delivery Inquiry"
            status_raw = _extract_mapped_value(raw_row, mapping, "status") or "Open"
            status_clean = "Resolved" if status_raw.strip().lower() in ("resolved", "closed", "done") else "Open"

            normalized = {
                "complaint_ref": _extract_mapped_value(raw_row, mapping, "complaint_ref") or f"WA-ROW-{row_number}",
                "complaint_timestamp_utc": ts_res.dt_utc.isoformat() if ts_res.dt_utc else None,
                "complaint_timestamp_iso": ts_res.dt_utc.isoformat() if ts_res.dt_utc else "",
                "customer_phone_raw": phone_raw,
                "customer_phone_norm": phone_norm if phone_valid else "",
                "customer_phone_masked": mask_phone(phone_norm if phone_valid else phone_raw),
                "customer_name": customer_name,
                "order_number_mentioned_raw": order_mentioned_raw,
                "order_number_mentioned_norm": order_mentioned_norm,
                "col_order_norm": col_order_norm,
                "text_order_norm": text_order_norm,
                "tracking_mentioned_norm": tracking_mentioned_norm,
                "issue_category": category,
                "message_text": msg_text,
                "status": status_clean,
                "claims_not_received": detect_claims_not_received(msg_text, category),
            }

        fingerprint = compute_row_fingerprint(source_type, normalized, raw_row)

        if errors:
            validation_status = "rejected"
            rejected_count += 1
            error_rows.append(
                {
                    "row_number": row_number,
                    "status": "rejected",
                    "reasons": "; ".join(errors),
                    "raw_data": raw_row,
                }
            )
        elif fingerprint in seen_fingerprints:
            validation_status = "duplicate"
            duplicate_count += 1
            error_rows.append(
                {
                    "row_number": row_number,
                    "status": "duplicate",
                    "reasons": "Duplicate row (already present in this file or previously imported).",
                    "raw_data": raw_row,
                }
            )
        else:
            validation_status = "accepted"
            accepted_count += 1
            seen_fingerprints.add(fingerprint)

        parsed_rows.append(
            ParsedRowRecord(
                row_number=row_number,
                raw_row=raw_row,
                normalized=normalized,
                row_fingerprint=fingerprint,
                validation_status=validation_status,
                errors=errors,
                has_ambiguous_date=has_ambiguous_date,
            )
        )

    total_rows = accepted_count + rejected_count + duplicate_count
    return CSVParseResult(
        source_type=source_type,
        filename=filename,
        file_hash=file_hash,
        encoding_used=encoding_used,
        headers=headers,
        column_mapping=mapping,
        missing_required_fields=missing_required,
        date_format_preference=date_format_preference,
        ambiguous_date_samples=ambiguous_samples,
        total_rows=total_rows,
        accepted_rows=accepted_count,
        rejected_rows=rejected_count,
        duplicate_rows=duplicate_count,
        sample_rows=sample_rows,
        parsed_rows=parsed_rows,
        error_rows=error_rows,
    )


def generate_error_report_csv(error_rows: list[dict[str, Any]], filename: str = "import.csv") -> str:
    """Create a downloadable CSV string explaining rejected and duplicate rows."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Source File", "Original Row Number", "Validation Status", "Reason", "Raw Row JSON"])
    for item in error_rows:
        writer.writerow(
            [
                sanitize_csv_cell(filename),
                sanitize_csv_cell(item.get("row_number", "")),
                sanitize_csv_cell(item.get("status", "")),
                sanitize_csv_cell(item.get("reasons", "")),
                sanitize_csv_cell(json.dumps(item.get("raw_data", {}), ensure_ascii=False)),
            ]
        )
    return output.getvalue()
