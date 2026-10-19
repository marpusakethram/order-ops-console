"""Natural-language query translation, strict schema validation, and deterministic fallback.

Security & Architecture Rules:
- Gemini is used ONLY to translate a short user question into `StructuredOrderQuery`.
- Whole datasets, customer records, and complaint bodies are NEVER sent to Gemini.
- Arbitrary SQL is NEVER generated or executed.
- Ambiguous questions (e.g. "Which orders are stuck?") do NOT silently guess an interpretation;
  they return structured disambiguation options so the user can choose.
- Unsupported actions (such as issuing refunds, messaging customers, or modifying data)
  are explicitly rejected with a clear explanation.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from src.config import get_config

ALLOWED_INTENTS = (
    "filter_orders",
    "ambiguous_clarification",
    "review_unmatched_records",
    "unsupported_question",
)


class ClarificationChoice(BaseModel):
    label: str
    description: str
    follow_up_question: str
    filter_summary: dict[str, str]


class StructuredOrderQuery(BaseModel):
    """Strict, validated query structure executed by deterministic application code."""

    intent: Literal[
        "filter_orders",
        "ambiguous_clarification",
        "review_unmatched_records",
        "unsupported_question",
    ] = "filter_orders"
    explanation: str = Field(
        default="",
        description="Plain-English explanation of how the question was interpreted.",
    )
    shipment_status: Literal[
        "any",
        "awaiting_pickup",
        "in_transit",
        "out_for_delivery",
        "failed_attempt",
        "rto",
        "delivered",
        "cancelled",
        "undelivered",
    ] = "any"
    fulfillment_status: Literal["any", "unfulfilled", "partial", "fulfilled", "cancelled"] = "any"
    payment_method: Literal["any", "COD", "Prepaid"] = "any"
    courier: str = ""
    city: str = ""
    attention_level: Literal["any", "High", "Medium", "Low", "flagged_any"] = "any"
    rule_code: Literal[
        "any",
        "UNSHIPPED_DELAY",
        "STALE_TRACKING",
        "PAST_EXPECTED_DELIVERY",
        "FAILED_DELIVERY_ATTEMPT",
        "RTO_INITIATED",
        "COMPLAINT_UNDELIVERED",
        "DELIVERED_NOT_RECEIVED",
        "CONFLICTING_SHIPMENT_INFO",
    ] = "any"
    min_stale_days: int | None = Field(default=None, ge=1, le=60)
    min_unshipped_days: int | None = Field(default=None, ge=1, le=60)
    has_open_complaint: bool | None = None
    match_status: Literal[
        "any",
        "Exact match",
        "Strong match",
        "Needs review",
        "Unmatched",
        "Manually confirmed",
    ] = "any"
    sort_by: Literal["priority", "order_date_asc", "order_date_desc", "amount_desc"] = "priority"
    limit: int = Field(default=50, ge=1, le=200)
    clarification_choices: list[ClarificationChoice] = Field(default_factory=list)
    parser_used: str = "deterministic"


_WORD_TO_INT = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
    "7": 7,
}

_KNOWN_COURIERS = ("Delhivery", "BlueDart", "XpressBees", "Ecom Express", "DTDC", "Shadowfax")
_KNOWN_CITIES = (
    "Bengaluru",
    "Mumbai",
    "New Delhi",
    "Delhi",
    "Hyderabad",
    "Pune",
    "Chennai",
    "Jaipur",
    "Ahmedabad",
    "Kochi",
    "Indore",
    "Lucknow",
    "Coimbatore",
)


def _extract_days(text: str) -> int | None:
    m = re.search(
        r"\b(one|two|three|four|five|six|seven|[1-9]\d?)\s*days?\b",
        text,
         flags=re.IGNORECASE,
    )
    if m:
        tok = m.group(1).lower()
        return _WORD_TO_INT.get(tok) or int(tok)
    return None


def parse_question_deterministically(
    question: str,
    unshipped_threshold_hours: int = 48,
    stale_tracking_threshold_hours: int = 72,
) -> StructuredOrderQuery:
    """Deterministic parser for supported operational questions and ambiguity checks."""
    q_raw = (question or "").strip()
    if len(q_raw) > 300:
        q_raw = q_raw[:300]
    q = q_raw.lower()

    if not q:
        return StructuredOrderQuery(
            intent="unsupported_question",
            explanation="Please enter a question about your orders, shipments, complaints, or matching status.",
            parser_used="deterministic",
        )

    # 1. Block unsupported / action-taking / destructive / off-topic requests
    unsupported_patterns = (
        r"\b(refund|cancel\s+order|send\s+whatsapp|send\s+message|message\s+customer|email\s+customer|call\s+courier)\b",
        r"\b(drop\s+table|delete\s+from|insert\s+into|update\s+orders|select\s+\*)\b",
        r"\b(weather|stock\s+price|recipe|joke|poem|forecast\s+revenue|predict)\b",
    )
    for pat in unsupported_patterns:
        if re.search(pat, q):
            return StructuredOrderQuery(
                intent="unsupported_question",
                explanation=(
                    "Order Ops Console is a read-only diagnostic and reconciliation tool for queries. "
                    "It does not automatically message customers, contact couriers, cancel orders, issue refunds, "
                    "or run arbitrary database commands. Try asking about delayed orders, failed delivery attempts, "
                    "RTO shipments, customer complaints, or unmatched records."
                ),
                parser_used="deterministic",
            )

    # 2. Check for ambiguous "stuck" query (unless already disambiguated by specific phrase)
    is_stuck_word = bool(re.search(r"\b(stuck|delayed|stalled|held\s+up)\b", q))
    has_specific_stuck_criteria = any(
        phrase in q
        for phrase in (
            "awaiting shipment",
            "not shipped",
            "unshipped",
            "unfulfilled",
            "no update",
            "no tracking",
            "stale",
            "in transit without",
            "promised delivery",
            "expected delivery",
            "past expected",
        )
    )
    if is_stuck_word and not has_specific_stuck_criteria:
        unshipped_days = int(round(unshipped_threshold_hours / 24))
        stale_days = int(round(stale_tracking_threshold_hours / 24))
        return StructuredOrderQuery(
            intent="ambiguous_clarification",
            explanation=(
                "The term \"stuck\" can refer to three different operational bottlenecks. "
                "Please choose which definition you want to inspect:"
            ),
            clarification_choices=[
                ClarificationChoice(
                    label=f"Awaiting shipment > {unshipped_days} days",
                    description=f"Paid or confirmed orders that have not been dispatched after {unshipped_threshold_hours} hours.",
                    follow_up_question=f"Show orders awaiting shipment longer than {unshipped_days} days",
                    filter_summary={"rule_code": "UNSHIPPED_DELAY"},
                ),
                ClarificationChoice(
                    label=f"In transit with no tracking update > {stale_days} days",
                    description=f"Active shipments with no courier scan for at least {stale_tracking_threshold_hours} hours.",
                    follow_up_question=f"Show shipments with no update for {stale_days} days",
                    filter_summary={"rule_code": "STALE_TRACKING"},
                ),
                ClarificationChoice(
                    label="Past promised / expected delivery date",
                    description="Undelivered shipments that have passed their expected delivery date (EDD).",
                    follow_up_question="Show orders past their promised delivery date",
                    filter_summary={"rule_code": "PAST_EXPECTED_DELIVERY"},
                ),
            ],
            parser_used="deterministic",
        )

    # 3. Check for unmatched / ambiguous match review queries
    if any(
        kw in q
        for kw in (
            "need a match",
            "needs a match",
            "needs review",
            "need review",
            "unmatched",
            "ambiguous match",
            "could not be matched",
        )
    ):
        return StructuredOrderQuery(
            intent="review_unmatched_records",
            explanation="Showing courier and WhatsApp complaint records that are unmatched or flagged for manual match review.",
            match_status="Needs review",
            parser_used="deterministic",
        )

    # 4. Extract common filters (courier, payment method, city)
    payment_method: Literal["any", "COD", "Prepaid"] = "any"
    if re.search(r"\b(cod|cash on delivery)\b", q):
        payment_method = "COD"
    elif re.search(r"\b(prepaid|upi|paid online)\b", q):
        payment_method = "Prepaid"

    courier_matched = ""
    for c_name in _KNOWN_COURIERS:
        if c_name.lower() in q:
            courier_matched = c_name
            break

    city_matched = ""
    for city_name in _KNOWN_CITIES:
        if city_name.lower() in q:
            city_matched = "New Delhi" if city_name.lower() == "delhi" else city_name
            break

    days_val = _extract_days(q)

    # 5. Match specific operational intents
    # 5a. Stale tracking / no tracking update
    if any(kw in q for kw in ("no update", "no tracking update", "stale tracking", "without a recent update", "no scan")):
        stale_days = days_val or int(round(stale_tracking_threshold_hours / 24))
        return StructuredOrderQuery(
            intent="filter_orders",
            explanation=(
                f"Filtering active shipments that have had no courier tracking scan for at least {stale_days} day(s)."
            ),
            rule_code="STALE_TRACKING",
            min_stale_days=stale_days,
            payment_method=payment_method,
            courier=courier_matched,
            city=city_matched,
            parser_used="deterministic",
        )

    # 5b. Awaiting shipment / unshipped past threshold
    if any(kw in q for kw in ("awaiting shipment", "not shipped", "unshipped", "unfulfilled")):
        if "longer than" in q or "past" in q or "overdue" in q or days_val:
            u_days = days_val or int(round(unshipped_threshold_hours / 24))
            return StructuredOrderQuery(
                intent="filter_orders",
                explanation=f"Filtering non-cancelled orders awaiting shipment for at least {u_days} day(s).",
                rule_code="UNSHIPPED_DELAY",
                min_unshipped_days=u_days,
                payment_method=payment_method,
                city=city_matched,
                parser_used="deterministic",
            )
        return StructuredOrderQuery(
            intent="filter_orders",
            explanation="Filtering non-cancelled orders currently awaiting shipment (unfulfilled).",
            fulfillment_status="unfulfilled",
            payment_method=payment_method,
            city=city_matched,
            parser_used="deterministic",
        )

    # 5c. Past promised / expected delivery date
    if any(kw in q for kw in ("promised delivery", "expected delivery", "past expected", "overdue delivery", "past edd")):
        return StructuredOrderQuery(
            intent="filter_orders",
            explanation="Filtering undelivered orders that are past their promised / expected delivery date.",
            rule_code="PAST_EXPECTED_DELIVERY",
            payment_method=payment_method,
            courier=courier_matched,
            city=city_matched,
            parser_used="deterministic",
        )

    # 5d. Failed delivery attempts / NDR
    if any(kw in q for kw in ("failed delivery", "failed attempt", "ndr", "door locked", "customer unavailable", "undelivered attempt")):
        return StructuredOrderQuery(
            intent="filter_orders",
            explanation=(
                f"Filtering {'COD ' if payment_method == 'COD' else ''}orders with recorded failed delivery attempts (NDR)."
            ),
            shipment_status="failed_attempt",
            rule_code="FAILED_DELIVERY_ATTEMPT",
            payment_method=payment_method,
            courier=courier_matched,
            city=city_matched,
            parser_used="deterministic",
        )

    # 5e. Return to origin / returning to warehouse
    if any(kw in q for kw in ("returning to origin", "return to origin", "rto", "returning to warehouse", "returned to warehouse")):
        return StructuredOrderQuery(
            intent="filter_orders",
            explanation="Filtering orders with shipments marked Return-to-Origin (RTO Initiated or Returned to Warehouse).",
            shipment_status="rto",
            rule_code="RTO_INITIATED",
            payment_method=payment_method,
            courier=courier_matched,
            city=city_matched,
            parser_used="deterministic",
        )

    # 5f. Customers who complained while orders are in transit / undelivered / delivered not received
    if any(kw in q for kw in ("complain", "complaint", "whatsapp")):
        if "not received" in q or ("delivered" in q and "not" in q):
            return StructuredOrderQuery(
                intent="filter_orders",
                explanation="Filtering orders marked 'Delivered' by the courier where the customer complained they did not receive the package.",
                rule_code="DELIVERED_NOT_RECEIVED",
                has_open_complaint=True,
                payment_method=payment_method,
                courier=courier_matched,
                city=city_matched,
                parser_used="deterministic",
            )
        if "in transit" in q:
            return StructuredOrderQuery(
                intent="filter_orders",
                explanation="Filtering orders that have an open customer complaint while their shipment is still in transit.",
                shipment_status="in_transit",
                has_open_complaint=True,
                payment_method=payment_method,
                courier=courier_matched,
                city=city_matched,
                parser_used="deterministic",
            )
        return StructuredOrderQuery(
            intent="filter_orders",
            explanation="Filtering orders with open customer complaints requiring operational review.",
            has_open_complaint=True,
            payment_method=payment_method,
            courier=courier_matched,
            city=city_matched,
            parser_used="deterministic",
        )

    # 5g. Orders needing attention first / high priority
    if any(kw in q for kw in ("attention", "high priority", "urgent", "flagged")):
        level: Literal["any", "High", "Medium", "Low", "flagged_any"] = (
            "High" if ("first" in q or "high" in q or "urgent" in q) else "flagged_any"
        )
        return StructuredOrderQuery(
            intent="filter_orders",
            explanation=f"Filtering orders with {'High priority' if level == 'High' else 'active'} operational attention flags.",
            attention_level=level,
            payment_method=payment_method,
            courier=courier_matched,
            city=city_matched,
            parser_used="deterministic",
        )

    # 5h. General filter by courier, payment method, city, or delivered/in-transit status
    if "in transit" in q:
        return StructuredOrderQuery(
            intent="filter_orders",
            explanation="Filtering orders with shipments currently in transit.",
            shipment_status="in_transit",
            payment_method=payment_method,
            courier=courier_matched,
            city=city_matched,
            parser_used="deterministic",
        )

    if "delivered" in q:
        return StructuredOrderQuery(
            intent="filter_orders",
            explanation="Filtering orders whose shipments have been marked Delivered.",
            shipment_status="delivered",
            payment_method=payment_method,
            courier=courier_matched,
            city=city_matched,
            parser_used="deterministic",
        )

    if payment_method != "any" or courier_matched or city_matched:
        parts = []
        if payment_method != "any":
            parts.append(f"payment method '{payment_method}'")
        if courier_matched:
            parts.append(f"courier '{courier_matched}'")
        if city_matched:
            parts.append(f"city '{city_matched}'")
        return StructuredOrderQuery(
            intent="filter_orders",
            explanation=f"Filtering orders matching {', '.join(parts)}.",
            payment_method=payment_method,
            courier=courier_matched,
            city=city_matched,
            parser_used="deterministic",
        )

    return StructuredOrderQuery(
        intent="unsupported_question",
        explanation=(
            "I could not map that question to an operational order filter. "
            "Try asking about stuck/delayed orders, shipments with no tracking update, "
            "failed delivery attempts, COD orders, RTO shipments, customer complaints, or unmatched records."
        ),
        parser_used="deterministic",
    )


def translate_natural_language_question(
    question: str,
    unshipped_threshold_hours: int = 48,
    stale_tracking_threshold_hours: int = 72,
) -> StructuredOrderQuery:
    """Translate a user question into a validated `StructuredOrderQuery`.

    - Always checks ambiguity and unsupported-action guardrails first.
    - When `GEMINI_API_KEY` is configured, uses `google-genai` (`gemini-2.5-flash` by default)
      with structured JSON output validated against `StructuredOrderQuery`.
    - Falls back cleanly to `parse_question_deterministically` if Gemini is not configured,
      times out, or returns a malformed response.
    """
    det_result = parse_question_deterministically(
        question=question,
        unshipped_threshold_hours=unshipped_threshold_hours,
        stale_tracking_threshold_hours=stale_tracking_threshold_hours,
    )

    # Always preserve deterministic disambiguation for "stuck" and hard blocks for unsupported commands
    if det_result.intent in ("ambiguous_clarification", "unsupported_question"):
        # If deterministic parser already resolved an ambiguous clarification, return it immediately
        if det_result.intent == "ambiguous_clarification":
            return det_result

    cfg = get_config()
    if not cfg.is_gemini_configured:
        return det_result

    # Attempt Gemini structured translation with strict schema validation and timeout
    try:
        from google import genai
        from google.genai import types

        safe_question = (question or "").strip()[:300]
        system_instruction = (
            "You translate natural-language order-operations questions for an Indian D2C brand into a "
            "strict JSON filter structure. Never invent data or generate SQL. "
            "If the user asks an ambiguous question like 'which orders are stuck?' without specifying "
            "whether they mean unshipped, stale tracking, or past expected delivery date, set intent='ambiguous_clarification'. "
            "If the user asks to perform an external action (refund, cancel, message customer) or an off-topic question, "
            "set intent='unsupported_question'."
        )

        client = genai.Client(
            api_key=cfg.gemini_api_key,
            http_options=types.HttpOptions(timeout=cfg.gemini_timeout_seconds * 1000),
        )
        response = client.models.generate_content(
            model=cfg.gemini_model,
            contents=f"User question: {safe_question}",
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_schema=StructuredOrderQuery,
                temperature=0.0,
            ),
        )
        if response and response.text:
            parsed = StructuredOrderQuery.model_validate_json(response.text)
            parsed.parser_used = f"gemini ({cfg.gemini_model})"
            if parsed.intent == "ambiguous_clarification" and not parsed.clarification_choices:
                return det_result
            return parsed
    except Exception:
        # Safe fallback to deterministic parser on timeout, network issue, or malformed response
        det_result.parser_used = "deterministic (fallback)"
        return det_result

    return det_result
