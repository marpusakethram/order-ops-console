"""Normalization, formatting, date validation, and CSV sanitization utilities."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# Common order number prefixes used across Shopify, ERPs, and courier sheets
_ORDER_PREFIX_RE = re.compile(
    r"^(?:ORDER\s*(?:NO|NUMBER|ID)?|ORD|SHP|SO|INV)[\s\-_:#/]+([A-Z0-9\-_]+)$",
    re.IGNORECASE,
)
_ORDER_INLINE_PREFIX_RE = re.compile(
    r"^(?:ORDER|ORD|SO)#?(\d[\dA-Z\-]*)$",
    re.IGNORECASE,
)

# Patterns to extract order numbers or tracking numbers mentioned inside complaint messages
_TEXT_ORDER_PATTERNS = [
    re.compile(r"(?:order\s*(?:no|number|id)?[\s:.\-#]*|#|ORD[\-\s#]?)([0-9]{3,10}[A-Z]?)\b", re.IGNORECASE),
    re.compile(r"\b(ORD[\-_]?[0-9]{3,10})\b", re.IGNORECASE),
]
_TEXT_AWB_PATTERNS = [
    re.compile(r"\b(?:awb|tracking|waybill)[\s:.\-#]*([A-Z0-9]{6,18})\b", re.IGNORECASE),
    re.compile(r"\b((?:DLV|BLD|XPB|ECM|DTC|SFX)[0-9]{6,14})\b", re.IGNORECASE),
]


def normalize_order_id(raw_value: str | None) -> str:
    """Normalize an order identifier while preserving leading zeros and meaningful characters.

    Examples:
      "#1042"       -> "1042"
      "ORD-1042"    -> "1042"
      "ORD-004210"  -> "004210"  (leading zeros preserved)
      "#004210"     -> "004210"
      "BLR-2026-09" -> "BLR-2026-09" (custom prefix preserved)
    """
    if raw_value is None:
        return ""
    cleaned = str(raw_value).strip()
    if not cleaned:
        return ""

    # Remove surrounding quotes or leading '#'
    cleaned = cleaned.strip("\"'").strip()
    while cleaned.startswith("#"):
        cleaned = cleaned[1:].strip()

    # Check explicit prefix with separator (e.g. "ORD-1042", "Order ID: 004210")
    m = _ORDER_PREFIX_RE.match(cleaned)
    if m:
        cleaned = m.group(1).strip()
        while cleaned.startswith("#"):
            cleaned = cleaned[1:].strip()
    else:
        m2 = _ORDER_INLINE_PREFIX_RE.match(cleaned)
        if m2:
            cleaned = m2.group(1).strip()

    # Collapse internal spaces/hyphens only around generic separators
    cleaned = re.sub(r"\s+", "", cleaned).upper()
    return cleaned


def normalize_tracking_number(raw_value: str | None) -> str:
    """Normalize a courier AWB / tracking number as a string, preserving leading zeros."""
    if raw_value is None:
        return ""
    cleaned = str(raw_value).strip().strip("\"'").strip()
    if not cleaned:
        return ""
    cleaned = re.sub(r"^(?:AWB|WAYBILL|TRACKING(?:\s*NO|\s*NUMBER)?)[\s:\-#]+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[\s\-]+", "", cleaned).upper()
    return cleaned


def extract_order_id_from_text(text: str | None) -> tuple[str, str]:
    """Extract (raw_mention, normalized_order_id) from free-text complaint messages."""
    if not text:
        return "", ""
    for pattern in _TEXT_ORDER_PATTERNS:
        match = pattern.search(text)
        if match:
            raw_mention = match.group(0).strip()
            norm = normalize_order_id(match.group(1))
            if norm and len(norm) >= 3:
                return raw_mention, norm
    return "", ""


def extract_tracking_from_text(text: str | None) -> str:
    """Extract normalized AWB/tracking number from free-text complaint messages."""
    if not text:
        return ""
    for pattern in _TEXT_AWB_PATTERNS:
        match = pattern.search(text)
        if match:
            norm = normalize_tracking_number(match.group(1))
            if norm and len(norm) >= 6:
                return norm
    return ""


def normalize_phone(raw_value: str | None) -> tuple[str, bool]:
    """Normalize a phone number carefully.

    Supports common Indian phone formats (+91, 91, 0 prefix, spaces, hyphens, parentheses)
    without assuming that non-Indian international numbers (+1, +44, +971, etc.) are Indian.

    Returns:
      (normalized_e164_phone, is_valid)
    """
    if raw_value is None:
        return "", False
    raw = str(raw_value).strip()
    if not raw:
        return "", False

    # Prevent scientific notation artifacts if someone exported float text
    if re.match(r"^\d+\.\d+E\+\d+$", raw, re.IGNORECASE):
        return "", False

    has_plus = raw.lstrip().startswith("+")
    has_00_prefix = raw.lstrip().startswith("00")

    # Strip common "(0)" trunk indicator used in some Indian spreadsheets, e.g. "+91 (0) 98765 43210"
    cleaned = re.sub(r"\(0\)", "", raw)
    digits = re.sub(r"\D", "", cleaned)

    if not digits:
        return "", False

    if has_00_prefix and len(digits) > 4:
        digits = digits[2:]
        has_plus = True

    if has_plus:
        # Explicit international prefix
        if digits.startswith("91"):
            national = digits[2:]
            if national.startswith("0") and len(national) == 11:
                national = national[1:]
            if len(national) == 10 and national[0] in "6789":
                return f"+91{national}", True
            return "", False
        # Non-Indian international number (e.g., +1, +44, +971, +65)
        if 8 <= len(digits) <= 15 and not digits.startswith("0"):
            return f"+{digits}", True
        return "", False

    # No explicit '+' sign
    if len(digits) == 10 and digits[0] in "6789":
        return f"+91{digits}", True
    if len(digits) == 11 and digits.startswith("0") and digits[1] in "6789":
        return f"+91{digits[1:]}", True
    if len(digits) == 12 and digits.startswith("91") and digits[2] in "6789":
        return f"+91{digits[2:]}", True

    return "", False


def mask_phone(phone_norm_or_raw: str | None) -> str:
    """Return a privacy-masked representation of a phone number for table display."""
    if not phone_norm_or_raw:
        return "—"
    norm, valid = normalize_phone(phone_norm_or_raw)
    target = norm if valid else str(phone_norm_or_raw).strip()
    digits = re.sub(r"\D", "", target)
    if len(digits) < 4:
        return "••••"
    last4 = digits[-4:]
    if target.startswith("+91") or (len(digits) == 10):
        return f"+91 •••••• {last4}"
    if target.startswith("+") and len(digits) > 10:
        cc = digits[: len(digits) - 10]
        return f"+{cc} •••••• {last4}"
    return f"•••••• {last4}"


def parse_inr_to_paise(raw_value: str | int | Decimal | None) -> tuple[int | None, str | None]:
    """Parse an Indian Rupee amount into integer paise using Decimal arithmetic (never binary float).

    Examples:
      "₹1,499.50" -> (149950, None)
      "Rs. 2,100" -> (210000, None)
      "899"       -> (89900, None)
    """
    if raw_value is None:
        return None, "Amount is missing"
    if isinstance(raw_value, int):
        return raw_value * 100, None
    cleaned = str(raw_value).strip()
    if not cleaned:
        return None, "Amount is blank"

    # Remove currency symbols (₹, INR, Rs., Rs) and thousand separators (commas, spaces)
    cleaned = re.sub(r"^(?:INR|RS\.?|₹)\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("₹", "").replace(",", "").strip()

    try:
        dec = Decimal(cleaned)
    except InvalidOperation:
        return None, f"Invalid monetary amount '{raw_value}'"

    if dec < 0:
        return None, f"Negative monetary amount '{raw_value}' is not allowed"

    paise_dec = (dec * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(paise_dec), None


def format_paise_to_inr(paise: int | None) -> str:
    """Format integer paise into Indian Rupee notation (e.g. ₹1,499 or ₹1,25,000.50)."""
    if paise is None:
        return "₹0"
    negative = paise < 0
    paise_abs = abs(int(paise))
    rupees = paise_abs // 100
    rem_paise = paise_abs % 100

    s = str(rupees)
    if len(s) > 3:
        last3 = s[-3:]
        rest = s[:-3]
        groups = []
        while len(rest) > 2:
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.insert(0, rest)
        formatted_rupees = ",".join(groups) + "," + last3
    else:
        formatted_rupees = s

    prefix = "-₹" if negative else "₹"
    if rem_paise > 0:
        return f"{prefix}{formatted_rupees}.{rem_paise:02d}"
    return f"{prefix}{formatted_rupees}"


@dataclass
class ParsedTimestampResult:
    dt_utc: datetime | None
    is_ambiguous: bool = False
    error_message: str | None = None
    assumed_ist: bool = False


_NUMERIC_DATE_RE = re.compile(
    r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)?)?$",
    re.IGNORECASE,
)


def parse_timestamp_ist(
    raw_value: str | None,
    date_format_preference: str = "auto",
) -> ParsedTimestampResult:
    """Parse a timestamp string into UTC while detecting ambiguous dates.

    Timezone rule:
      - If the timestamp includes an explicit timezone offset, convert it to UTC.
      - If the timestamp has no timezone offset, assume Asia/Kolkata (IST, UTC+05:30).

    Ambiguous date rule:
      - For numeric dates like '04/05/2026' where both first and second numbers are <= 12
        and not equal, if `date_format_preference == "auto"`, return `is_ambiguous=True`
        instead of silently guessing between DD/MM/YYYY and MM/DD/YYYY.
    """
    if raw_value is None:
        return ParsedTimestampResult(dt_utc=None, error_message="Timestamp is missing")
    text = str(raw_value).strip()
    if not text:
        return ParsedTimestampResult(dt_utc=None, error_message="Timestamp is blank")

    pref = (date_format_preference or "auto").strip().upper()

    # 1. Check numeric slash/dash dates first (DD/MM/YYYY vs MM/DD/YYYY)
    m = _NUMERIC_DATE_RE.match(text)
    if m:
        p1, p2, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hour = int(m.group(4)) if m.group(4) is not None else 0
        minute = int(m.group(5)) if m.group(5) is not None else 0
        second = int(m.group(6)) if m.group(6) is not None else 0
        ampm = (m.group(7) or "").upper()

        if ampm == "PM" and hour < 12:
            hour += 12
        elif ampm == "AM" and hour == 12:
            hour = 0

        if 1 <= p1 <= 12 and 1 <= p2 <= 12 and p1 != p2:
            if pref == "AUTO":
                return ParsedTimestampResult(
                    dt_utc=None,
                    is_ambiguous=True,
                    error_message=(
                        f"Ambiguous date '{text}': both {p1:02d} and {p2:02d} could be day or month. "
                        "Select DD/MM/YYYY or MM/DD/YYYY to resolve."
                    ),
                )
            if pref == "MM/DD/YYYY":
                month, day = p1, p2
            else:
                day, month = p1, p2
        elif p1 > 12 and 1 <= p2 <= 12:
            if pref == "MM/DD/YYYY":
                return ParsedTimestampResult(
                    dt_utc=None,
                    error_message=f"Date '{text}' has first part {p1} > 12, which does not match MM/DD/YYYY.",
                )
            day, month = p1, p2
        elif p2 > 12 and 1 <= p1 <= 12:
            if pref == "DD/MM/YYYY":
                return ParsedTimestampResult(
                    dt_utc=None,
                    error_message=f"Date '{text}' has second part {p2} > 12, which does not match DD/MM/YYYY.",
                )
            month, day = p1, p2
        elif p1 == p2 and 1 <= p1 <= 12:
            day, month = p1, p2
        else:
            return ParsedTimestampResult(
                dt_utc=None,
                error_message=f"Invalid calendar date '{text}'.",
            )

        try:
            dt_ist = datetime(year, month, day, hour, minute, second, tzinfo=IST)
            return ParsedTimestampResult(dt_utc=dt_ist.astimezone(timezone.utc), assumed_ist=True)
        except ValueError as exc:
            return ParsedTimestampResult(dt_utc=None, error_message=f"Invalid date '{text}': {exc}")

    # 2. Check ISO-8601 format (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS+05:30)
    iso_candidate = text.replace("Z", "+00:00")
    if re.match(r"^\d{4}-\d{2}-\d{2}", iso_candidate):
        try:
            dt = datetime.fromisoformat(iso_candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=IST)
                return ParsedTimestampResult(dt_utc=dt.astimezone(timezone.utc), assumed_ist=True)
            return ParsedTimestampResult(dt_utc=dt.astimezone(timezone.utc), assumed_ist=False)
        except ValueError:
            pass

    # 3. Check unambiguous month-name formats (e.g. "15 Oct 2026 14:30", "Oct 15, 2026")
    month_formats = [
        "%d %b %Y %H:%M:%S",
        "%d %b %Y %H:%M",
        "%d %b %Y %I:%M %p",
        "%d %b %Y",
        "%d %B %Y %H:%M",
        "%d %B %Y",
        "%b %d, %Y %H:%M",
        "%b %d, %Y",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d",
    ]
    clean_tz = re.sub(r"\s+(?:IST|Asia/Kolkata)$", "", text, flags=re.IGNORECASE).strip()
    for fmt in month_formats:
        try:
            dt = datetime.strptime(clean_tz, fmt).replace(tzinfo=IST)
            return ParsedTimestampResult(dt_utc=dt.astimezone(timezone.utc), assumed_ist=True)
        except ValueError:
            continue

    return ParsedTimestampResult(
        dt_utc=None,
        error_message=f"Unrecognized date/time format '{text}'. Use YYYY-MM-DD, DD/MM/YYYY, or '15 Oct 2026 14:30'.",
    )


def format_ist(dt: datetime | None, include_time: bool = True) -> str:
    """Format a datetime in Asia/Kolkata (IST) for consistent UI display."""
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ist_dt = dt.astimezone(IST)
    if include_time:
        return ist_dt.strftime("%d %b %Y, %I:%M %p IST")
    return ist_dt.strftime("%d %b %Y")


def to_ist_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).isoformat()


# Shipment status normalization dictionary
_SHIPMENT_STATUS_MAP: list[tuple[str, tuple[str, ...]]] = [
    (
        "rto_delivered",
        (
            "rto delivered",
            "returned to warehouse",
            "returned to origin delivered",
            "returned to shipper",
            "rto completed",
        ),
    ),
    (
        "rto_initiated",
        (
            "rto",
            "rto initiated",
            "rto in transit",
            "return to origin",
            "returning to origin",
            "returning to warehouse",
            "rto marked",
            "rto ofd",
        ),
    ),
    (
        "failed_attempt",
        (
            "failed attempt",
            "delivery attempt failed",
            "undelivered",
            "ndr",
            "customer unavailable",
            "consignee unavailable",
            "door locked",
            "house locked",
            "address incomplete",
            "customer refused",
            "consignee refused",
            "cod not ready",
            "delivery rescheduled",
        ),
    ),
    (
        "delivered",
        (
            "delivered",
            "dlvd",
            "successfully delivered",
            "delivered to consignee",
        ),
    ),
    (
        "out_for_delivery",
        (
            "out for delivery",
            "ofd",
            "with delivery courier",
            "out for dlv",
        ),
    ),
    (
        "in_transit",
        (
            "in transit",
            "in-transit",
            "picked up",
            "shipment picked up",
            "dispatched",
            "reached hub",
            "arrived at hub",
            "bagged",
            "forwarded",
            "in transit - hub",
            "connected",
        ),
    ),
    (
        "awaiting_pickup",
        (
            "manifested",
            "pickup scheduled",
            "awaiting pickup",
            "ready to ship",
            "shipment booked",
            "label created",
            "awb assigned",
        ),
    ),
    (
        "cancelled",
        (
            "cancelled",
            "canceled",
            "shipment cancelled",
            "pickup cancelled",
        ),
    ),
]


def normalize_shipment_status(raw_status: str | None) -> str:
    """Map courier status strings into canonical lifecycle states."""
    if not raw_status:
        return "unknown"
    cleaned = re.sub(r"[\-_]+", " ", str(raw_status).strip().lower())
    cleaned = re.sub(r"\s+", " ", cleaned)

    # Check exact or substring matches in priority order (e.g. "rto delivered" before "delivered")
    for norm_status, keywords in _SHIPMENT_STATUS_MAP:
        for kw in keywords:
            if cleaned == kw or kw in cleaned:
                # Guard: "undelivered" must not match "delivered"
                if norm_status == "delivered" and ("undelivered" in cleaned or "not delivered" in cleaned):
                    continue
                return norm_status
    return "unknown"


def format_shipment_status_label(status_norm: str | None) -> str:
    """Return a clean human-readable badge label for a normalized shipment status."""
    labels = {
        "awaiting_pickup": "Awaiting Pickup",
        "in_transit": "In Transit",
        "out_for_delivery": "Out for Delivery",
        "failed_attempt": "Failed Attempt (NDR)",
        "rto_initiated": "Returning to Origin (RTO)",
        "rto_delivered": "Returned to Warehouse",
        "delivered": "Delivered",
        "cancelled": "Cancelled",
        "unknown": "Unknown Status",
    }
    return labels.get(status_norm or "", "No Shipment")


def normalize_payment_method(raw_payment: str | None, financial_status: str | None = None) -> str:
    """Normalize payment method into 'COD' or 'Prepaid'."""
    text = f"{raw_payment or ''} {financial_status or ''}".strip().lower()
    if any(tok in text for tok in ("cod", "cash on delivery", "cash_on_delivery", "pay on delivery")):
        return "COD"
    return "Prepaid"


def normalize_fulfillment_status(raw_status: str | None, is_cancelled: bool = False) -> str:
    """Normalize Shopify fulfillment status."""
    if is_cancelled:
        return "cancelled"
    if not raw_status:
        return "unfulfilled"
    cleaned = str(raw_status).strip().lower()
    if cleaned in ("cancelled", "canceled", "voided", "restocked"):
        return "cancelled"
    if "partial" in cleaned:
        return "partial"
    if cleaned in ("fulfilled", "shipped", "completed", "complete"):
        return "fulfilled"
    return "unfulfilled"


def detect_claims_not_received(message_text: str | None, category: str | None = None) -> bool:
    """Detect whether a customer complaint claims an order was marked delivered/Fake delivery or not received."""
    combined = f"{message_text or ''} {category or ''}".lower()
    patterns = (
        "not received",
        "did not receive",
        "didn't receive",
        "hasn't arrived",
        "not delivered",
        "marked delivered",
        "says delivered",
        "shows delivered",
        "fake delivery",
        "still haven't got",
        "where is my order",
        "not yet delivered",
    )
    return any(p in combined for p in patterns)


def sanitize_csv_cell(value: object) -> str:
    """Protect exported CSV cells against spreadsheet formula injection (CSV Injection / DDE).

    Any string starting with '=', '+', '-', '@', tab, or carriage return is prefixed
    with a single quote so spreadsheet applications treat it as literal text.
    """
    if value is None:
        return ""
    text = str(value)
    stripped = text.lstrip(" ")
    if stripped and stripped[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{text}"
    return text
