"""Comprehensive business logic and API integration tests for Order Ops Console."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import jwt
import pytest
from fastapi.testclient import TestClient

from src.app import create_app
from src.csv_parser import parse_and_validate_csv, suggest_column_mapping
from src.database import Base, get_engine, init_engine
from src.nl_query import translate_natural_language_question
from src.normalization import (
    format_paise_to_inr,
    mask_phone,
    normalize_order_id,
    normalize_phone,
    normalize_shipment_status,
    normalize_tracking_number,
    parse_inr_to_paise,
    parse_timestamp_ist,
    sanitize_csv_cell,
)

IST = ZoneInfo("Asia/Kolkata")
FIXED_REF_ISO = "2026-10-19T18:00:00+05:30"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Create a fresh isolated SQLite test database and FastAPI TestClient per test."""
    db_file = tmp_path / "test_order_ops.db"
    db_url = f"sqlite:///{db_file.as_posix()}"

    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("APP_MODE", "demo")
    monkeypatch.setenv("DEFAULT_WORKSPACE_ID", "test-ws")
    monkeypatch.setenv("REFERENCE_TIME_MODE", "demo_fixed")
    monkeypatch.setenv("DEMO_REFERENCE_TIME", FIXED_REF_ISO)
    monkeypatch.setenv("REQUIRE_AUTH", "false")
    monkeypatch.setenv("APP_ACCESS_TOKEN", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")

    init_engine(db_url)
    Base.metadata.create_all(bind=get_engine())

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def _make_csv_bytes(headers: list[str], rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for r in rows:
        writer.writerow(r)
    return buf.getvalue().encode("utf-8")


# ============================================================================
# 1. Normalization & Validation Unit Tests
# ============================================================================

def test_identifier_and_phone_normalization():
    # Order IDs preserve leading zeros and meaningful custom prefixes
    assert normalize_order_id("#01004") == "01004"
    assert normalize_order_id("ORD-01004") == "01004"
    assert normalize_order_id("Order ID: #004210") == "004210"
    assert normalize_order_id("BLR-2026-09") == "BLR-2026-09"

    # Tracking numbers preserve leading zeros
    assert normalize_tracking_number("AWB: 00BLD10001") == "00BLD10001"
    assert normalize_tracking_number("dlv-0098-401") == "DLV0098401"

    # Indian phone numbers in varied formats normalize to +91XXXXXXXXXX
    assert normalize_phone("+91 98450-22334") == ("+919845022334", True)
    assert normalize_phone("09845022334") == ("+919845022334", True)
    assert normalize_phone("91 98450 22334") == ("+919845022334", True)
    assert normalize_phone("+91 (0) 98450 22334") == ("+919845022334", True)
    assert normalize_phone("9845022334") == ("+919845022334", True)

    # Non-Indian international numbers are NOT assumed to be Indian
    assert normalize_phone("+971 50 443 9981") == ("+971504439981", True)
    assert normalize_phone("+1 (415) 555-2671") == ("+14155552671", True)

    # Invalid short numbers fail validation
    assert normalize_phone("12345") == ("", False)

    # Masking hides middle digits
    assert mask_phone("+919845022334") == "+91 •••••• 2334"


def test_money_decimal_paise_conversion():
    paise, err = parse_inr_to_paise("₹1,499.50")
    assert err is None
    assert paise == 149950
    assert format_paise_to_inr(paise) == "₹1,499.50"

    paise2, err2 = parse_inr_to_paise("Rs. 1,25,000")
    assert err2 is None
    assert paise2 == 12500000
    assert format_paise_to_inr(paise2) == "₹1,25,000"

    bad_paise, bad_err = parse_inr_to_paise("NOT_A_NUMBER")
    assert bad_paise is None
    assert bad_err is not None


def test_ambiguous_date_detection_and_ist_assumption():
    # 04/05/2026 in 'auto' mode must be flagged as ambiguous rather than guessed silently
    res_auto = parse_timestamp_ist("04/05/2026 10:30", date_format_preference="auto")
    assert res_auto.is_ambiguous is True
    assert res_auto.dt_utc is None
    assert "Ambiguous date" in (res_auto.error_message or "")

    # Explicitly choosing DD/MM/YYYY resolves to 4 May 2026 10:30 IST (05:00 UTC)
    res_dmy = parse_timestamp_ist("04/05/2026 10:30", date_format_preference="DD/MM/YYYY")
    assert res_dmy.is_ambiguous is False
    assert res_dmy.dt_utc == datetime(2026, 5, 4, 5, 0, tzinfo=timezone.utc)

    # Explicitly choosing MM/DD/YYYY resolves to 5 April 2026 10:30 IST (05:00 UTC)
    res_mdy = parse_timestamp_ist("04/05/2026 10:30", date_format_preference="MM/DD/YYYY")
    assert res_mdy.is_ambiguous is False
    assert res_mdy.dt_utc == datetime(2026, 4, 5, 5, 0, tzinfo=timezone.utc)

    # Unambiguous date (19/10/2026 where 19 > 12) parses cleanly even in 'auto' mode
    res_unambig = parse_timestamp_ist("19/10/2026 18:00", date_format_preference="auto")
    assert res_unambig.is_ambiguous is False
    assert res_unambig.dt_utc == datetime(2026, 10, 19, 12, 30, tzinfo=timezone.utc)


def test_column_mapping_and_required_field_validation():
    headers = ["Order Number", "Created At", "Customer Mobile", "Grand Total", "Extra Ignored Col"]
    mapping = suggest_column_mapping("shopify_orders", headers)
    assert mapping["order_id"] == "Order Number"
    assert mapping["order_date"] == "Created At"
    assert mapping["customer_phone"] == "Customer Mobile"
    assert mapping["total_amount"] == "Grand Total"

    # Missing required field ("Total") triggers missing_required_fields
    incomplete_csv = _make_csv_bytes(
        ["Order Number", "Created At"],
        [["#1001", "15/10/2026 10:00"]],
    )
    parsed = parse_and_validate_csv(
        raw_bytes=incomplete_csv,
        filename="missing_total.csv",
        source_type="shopify_orders",
        date_format_preference="DD/MM/YYYY",
    )
    assert any(f["field"] == "total_amount" for f in parsed.missing_required_fields)
    assert parsed.rejected_rows == 1


# ============================================================================
# 2. Consolidation, Matching, Idempotency & Attention Rule Tests
# ============================================================================

def test_shopify_line_item_consolidation_and_duplicate_idempotency(client: TestClient):
    shopify_csv = _make_csv_bytes(
        [
            "Name", "Created at", "Customer Name", "Customer Phone",
            "Shipping City", "Total", "Payment Method", "Fulfillment Status",
            "Lineitem SKU", "Lineitem name", "Lineitem quantity", "Lineitem price",
            "Tracking Number", "Tracking Company",
        ],
        [
            # Order #05001 has 2 distinct line items + 1 exact duplicate row
            ["#05001", "16/10/2026 10:00", "Aarav Nair", "+91 98111-22233", "Bengaluru", "₹2,098.00", "Prepaid", "fulfilled", "SKU-1", "Kurta", "1", "1499.00", "DLV005001", "Delhivery"],
            ["#05001", "16/10/2026 10:00", "Aarav Nair", "+91 98111-22233", "Bengaluru", "₹2,098.00", "Prepaid", "fulfilled", "SKU-2", "Chai Tin", "1", "599.00", "DLV005001", "Delhivery"],
            ["#05001", "16/10/2026 10:00", "Aarav Nair", "+91 98111-22233", "Bengaluru", "₹2,098.00", "Prepaid", "fulfilled", "SKU-1", "Kurta", "1", "1499.00", "DLV005001", "Delhivery"],
        ],
    )

    resp = client.post(
        "/api/import/commit",
        files={"file": ("shopify.csv", shopify_csv, "text/csv")},
        data={"source_type": "shopify_orders", "date_format_preference": "DD/MM/YYYY"},
    )
    assert resp.status_code == 200
    summary = resp.json()
    assert summary["accepted_rows"] == 2
    assert summary["duplicate_rows"] == 1
    assert summary["created_orders"] == 1
    assert summary["consolidated_line_items"] == 2

    # Re-uploading the exact same file must be 100% idempotent (0 new orders, all rows marked duplicate)
    resp2 = client.post(
        "/api/import/commit",
        files={"file": ("shopify.csv", shopify_csv, "text/csv")},
        data={"source_type": "shopify_orders", "date_format_preference": "DD/MM/YYYY"},
    )
    assert resp2.status_code == 200
    summary2 = resp2.json()
    assert summary2["accepted_rows"] == 0
    assert summary2["duplicate_rows"] == 3
    assert summary2["reconciliation"]["orders_total"] == 1

    # Verify order total was not double-counted
    orders_list = client.get("/api/orders").json()
    assert orders_list["total"] == 1
    assert orders_list["items"][0]["order_value_inr"] == "₹2,098"


def test_multiple_shipments_and_event_chronological_ordering(client: TestClient):
    shopify_csv = _make_csv_bytes(
        ["Name", "Created at", "Customer Name", "Customer Phone", "Shipping City", "Total", "Payment Method", "Fulfillment Status"],
        [["#06001", "15/10/2026 10:00", "Divya Menon", "9822211100", "Kochi", "3500.00", "Prepaid", "fulfilled"]],
    )
    client.post(
        "/api/import/commit",
        files={"file": ("shopify.csv", shopify_csv, "text/csv")},
        data={"source_type": "shopify_orders", "date_format_preference": "DD/MM/YYYY"},
    )

    # Courier CSV has 2 different AWBs for #06001, and on AWB-2 the newer event ("Out for Delivery")
    # appears BEFORE the older event ("Picked Up")
    courier_csv = _make_csv_bytes(
        ["AWB", "Courier", "Order ID", "Status", "Event Time", "Location"],
        [
            ["BLD6001A", "BlueDart", "ORD-06001", "Delivered", "17/10/2026 15:00", "Kochi Hub"],
            ["DLV6001B", "Delhivery", "ORD-06001", "Out for Delivery", "19/10/2026 09:30", "Kochi Aluva Hub"],
            ["DLV6001B", "Delhivery", "ORD-06001", "Picked Up", "16/10/2026 12:00", "Bengaluru Origin"],
        ],
    )
    c_resp = client.post(
        "/api/import/commit",
        files={"file": ("courier.csv", courier_csv, "text/csv")},
        data={"source_type": "courier_tracking", "date_format_preference": "DD/MM/YYYY"},
    )
    assert c_resp.status_code == 200

    orders = client.get("/api/orders").json()["items"]
    assert len(orders) == 1
    order_id = orders[0]["id"]
    assert orders[0]["shipment_count"] == 2

    details = client.get(f"/api/orders/{order_id}").json()
    dlv_shp = next(s for s in details["shipments"] if s["tracking_number"] == "DLV6001B")
    # Latest status must remain "Out for Delivery" (the newer 19 Oct event), NOT overwritten by the 16 Oct event!
    assert dlv_shp["current_status_norm"] == "out_for_delivery"
    assert len(dlv_shp["events"]) == 2
    assert dlv_shp["events"][0]["status_norm"] == "in_transit"  # Picked Up (16 Oct) is first in timeline
    assert dlv_shp["events"][1]["status_norm"] == "out_for_delivery"  # Out for Delivery (19 Oct) is second


def test_ambiguous_matches_conflicting_ids_and_manual_review(client: TestClient):
    # Create 4 orders:
    # #07001 & #07002 share household phone +91 98333 44455
    # #07003 & #07004 have different phones; courier row will have Order #07003 but Phone of #07004
    shopify_csv = _make_csv_bytes(
        ["Name", "Created at", "Customer Name", "Customer Phone", "Shipping City", "Total", "Payment Method", "Fulfillment Status"],
        [
            ["#07001", "15/10/2026 10:00", "Kavya Sharma", "+91 98333 44455", "Mumbai", "1499.00", "Prepaid", "fulfilled"],
            ["#07002", "16/10/2026 11:00", "Arjun Sharma", "09833344455", "Mumbai", "1299.00", "COD", "fulfilled"],
            ["#07003", "15/10/2026 12:00", "Rahul Kulkarni", "+91 98444 11111", "Pune", "1299.00", "Prepaid", "fulfilled"],
            ["#07004", "15/10/2026 13:00", "Rahul Kulkarni", "+91 98444 22222", "Bengaluru", "1299.00", "COD", "fulfilled"],
        ],
    )
    client.post(
        "/api/import/commit",
        files={"file": ("shopify.csv", shopify_csv, "text/csv")},
        data={"source_type": "shopify_orders", "date_format_preference": "DD/MM/YYYY"},
    )

    courier_csv = _make_csv_bytes(
        ["AWB", "Courier", "Order ID", "Status", "Event Time", "Phone", "Customer Name", "Amount"],
        [
            # 1. Conflicting IDs: Order ID #07003 but Phone of #07004 -> Needs review
            ["AWB-CONFLICT-1", "Delhivery", "#07003", "In Transit", "18/10/2026 10:00", "+91 98444 22222", "Rahul Kulkarni", "1299.00"],
            # 2. Customer name only ("Rahul Kulkarni") without Order ID or Phone -> Never auto-matched!
            ["AWB-NAME-ONLY", "DTDC", "", "In Transit", "18/10/2026 11:00", "", "Rahul Kulkarni", "1299.00"],
            # 3. Order amount only (1299.00) without Order ID, Phone, or Name -> Unmatched!
            ["AWB-AMOUNT-ONLY", "BlueDart", "", "In Transit", "18/10/2026 12:00", "", "", "1299.00"],
        ],
    )
    client.post(
        "/api/import/commit",
        files={"file": ("courier.csv", courier_csv, "text/csv")},
        data={"source_type": "courier_tracking", "date_format_preference": "DD/MM/YYYY"},
    )

    # Complaint from shared household phone (+91 98333 44455) without Order ID -> Needs review
    whatsapp_csv = _make_csv_bytes(
        ["Ticket ID", "Timestamp", "Phone", "Sender Name", "Message"],
        [
            ["WA-SHARED-1", "19/10/2026 11:00", "9833344455", "Sharma Family", "Where is our Mumbai parcel?"],
        ],
    )
    client.post(
        "/api/import/commit",
        files={"file": ("whatsapp.csv", whatsapp_csv, "text/csv")},
        data={"source_type": "whatsapp_complaints", "date_format_preference": "DD/MM/YYYY"},
    )

    review_q = client.get("/api/match-review").json()
    assert review_q["counts"]["needs_review"] == 3
    assert review_q["counts"]["unmatched"] == 1

    # Find the shared-phone complaint and manually confirm it to Order #07002
    shared_item = next(i for i in review_q["items"] if i["source_type"] == "whatsapp_complaints")
    cand_7002 = next(c for c in shared_item["candidates"] if c["order_number"] == "#07002")

    dec_resp = client.post(
        "/api/match-review/decide",
        json={
            "raw_record_id": shared_item["raw_record_id"],
            "decision_type": "confirm_match",
            "selected_order_id": cand_7002["order_id"],
            "note": "Confirmed with customer that inquiry is for COD order #07002",
        },
    )
    assert dec_resp.status_code == 200
    assert dec_resp.json()["new_status"] == "Manually confirmed"

    # Verify audit history recorded the decision and raw payload was preserved
    review_after = client.get("/api/match-review").json()
    assert len(review_after["audit_history"]) == 1
    assert review_after["audit_history"][0]["selected_order_number"] == "#07002"
    updated_item = next(i for i in review_after["items"] if i["raw_record_id"] == shared_item["raw_record_id"])
    assert updated_item["raw_payload"]["Message"] == "Where is our Mumbai parcel?"

    # Now test changing/overriding a previous manual match to #07001
    cand_7001 = next(c for c in shared_item["candidates"] if c["order_number"] == "#07001")
    override_resp = client.post(
        "/api/match-review/decide",
        json={
            "raw_record_id": shared_item["raw_record_id"],
            "decision_type": "change_match",
            "selected_order_id": cand_7001["order_id"],
            "note": "Corrected match to #07001",
        },
    )
    assert override_resp.status_code == 200
    assert override_resp.json()["selected_order_number"] == "#07001"


def test_attention_rules_cancelled_delivered_and_thresholds(client: TestClient):
    # Reference time is 2026-10-19 18:00 IST
    shopify_csv = _make_csv_bytes(
        ["Name", "Created at", "Customer Name", "Customer Phone", "Shipping City", "Total", "Payment Method", "Fulfillment Status", "Cancelled at"],
        [
            # 1. Cancelled order from 6 days ago -> Must NOT be flagged as awaiting shipment!
            ["#08001", "13/10/2026 10:00", "Cancelled User", "9855500001", "Jaipur", "999.00", "COD", "cancelled", "13/10/2026 11:00"],
            # 2. Old unshipped paid order from 4 days ago (96h > 48h threshold) -> Flagged UNSHIPPED_DELAY
            ["#08002", "15/10/2026 12:00", "Delayed Unshipped", "9855500002", "Chennai", "1499.00", "Prepaid", "unfulfilled", ""],
            # 3. Delivered order from 5 days ago -> Must NOT be flagged as stale tracking!
            ["#08003", "12/10/2026 10:00", "Happy Customer", "9855500003", "Bengaluru", "1299.00", "Prepaid", "fulfilled", ""],
            # 4. Stale in-transit order (last scan 15 Oct 10:00 -> 104h > 72h threshold) -> Flagged STALE_TRACKING
            ["#08004", "13/10/2026 10:00", "Stale Transit", "9855500004", "Mumbai", "1999.00", "Prepaid", "fulfilled", ""],
            # 5. Delivered order where customer complains "not received" -> Flagged DELIVERED_NOT_RECEIVED
            ["#08005", "12/10/2026 10:00", "Disputed Delivery", "9855500005", "Pune", "2499.00", "Prepaid", "fulfilled", ""],
        ],
    )
    client.post(
        "/api/import/commit",
        files={"file": ("shopify.csv", shopify_csv, "text/csv")},
        data={"source_type": "shopify_orders", "date_format_preference": "DD/MM/YYYY"},
    )

    courier_csv = _make_csv_bytes(
        ["AWB", "Courier", "Order ID", "Status", "Event Time", "Location"],
        [
            ["AWB8003", "BlueDart", "#08003", "Delivered", "14/10/2026 14:00", "Bengaluru"],
            ["AWB8004", "Delhivery", "#08004", "In Transit", "15/10/2026 10:00", "Bhiwandi Hub"],
            ["AWB8005", "BlueDart", "#08005", "Delivered", "18/10/2026 16:00", "Pune"],
        ],
    )
    client.post(
        "/api/import/commit",
        files={"file": ("courier.csv", courier_csv, "text/csv")},
        data={"source_type": "courier_tracking", "date_format_preference": "DD/MM/YYYY"},
    )

    whatsapp_csv = _make_csv_bytes(
        ["Ticket ID", "Timestamp", "Phone", "Sender Name", "Order Ref", "Message"],
        [
            ["WA-8005", "19/10/2026 10:00", "9855500005", "Disputed Delivery", "#08005", "Tracking says delivered for #08005 but I have not received my parcel!"],
        ],
    )
    client.post(
        "/api/import/commit",
        files={"file": ("whatsapp.csv", whatsapp_csv, "text/csv")},
        data={"source_type": "whatsapp_complaints", "date_format_preference": "DD/MM/YYYY"},
    )

    items = {r["order_number"]: r for r in client.get("/api/orders").json()["items"]}

    # Cancelled order #08001 has NO flag
    assert items["#08001"]["attention_level"] == "No current flag"
    # Delivered order #08003 has NO flag
    assert items["#08003"]["attention_level"] == "No current flag"
    # Unshipped order #08002 has High UNSHIPPED_DELAY
    assert items["#08002"]["attention_level"] == "High"
    assert "UNSHIPPED_DELAY" in items["#08002"]["rule_codes"]
    # Stale tracking order #08004 has STALE_TRACKING
    assert "STALE_TRACKING" in items["#08004"]["rule_codes"]
    assert "threshold is 3 days" in items["#08004"]["reason_for_attention"]
    # Disputed delivery #08005 has High DELIVERED_NOT_RECEIVED
    assert items["#08005"]["attention_level"] == "High"
    assert "DELIVERED_NOT_RECEIVED" in items["#08005"]["rule_codes"]


# ============================================================================
# 3. Natural-Language Query, Gemini Fallback, Security & CSV Export Safety Tests
# ============================================================================

def test_nl_query_ambiguity_supported_unsupported_and_gemini_fallback(client: TestClient, monkeypatch):
    # Load full synthetic demo dataset
    demo_resp = client.post("/api/demo/load")
    assert demo_resp.status_code == 200
    assert demo_resp.json()["reconciliation"]["orders_total"] == 240

    # 1. Ambiguous query ("Which orders are stuck?") offers choices rather than guessing
    q1 = client.post("/api/ask", json={"question": "Which orders are stuck?"}).json()
    assert q1["is_ambiguous"] is True
    assert len(q1["clarification_choices"]) == 3

    # 2. Supported query: "Show shipments with no update for three days."
    q2 = client.post("/api/ask", json={"question": "Show shipments with no update for three days."}).json()
    assert q2["is_ambiguous"] is False
    assert q2["total_results"] > 0
    assert all("STALE_TRACKING" in r["rule_codes"] for r in q2["results"])

    # 3. Supported query: "Which COD orders had a failed delivery attempt?"
    q3 = client.post("/api/ask", json={"question": "Which COD orders had a failed delivery attempt?"}).json()
    assert q3["total_results"] > 0
    assert all(r["payment_method"] == "COD" for r in q3["results"])

    # 4. Supported query: "Show orders returning to origin."
    q4 = client.post("/api/ask", json={"question": "Show orders returning to origin."}).json()
    assert q4["total_results"] > 0

    # 5. Unsupported action / destructive query is blocked safely
    q5 = client.post("/api/ask", json={"question": "Issue a refund and send a WhatsApp message to all customers"}).json()
    assert q5["is_unsupported"] is True
    assert q5["total_results"] == 0

    # 6. Gemini fallback when GEMINI_API_KEY is set to a dummy value (network/API failure falls back cleanly)
    monkeypatch.setenv("GEMINI_API_KEY", "invalid-test-key-for-fallback-verification")
    fallback_res = translate_natural_language_question("Show orders returning to origin.")
    assert fallback_res.intent == "filter_orders"
    assert fallback_res.rule_code == "RTO_INITIATED"
    assert "fallback" in fallback_res.parser_used


def test_csv_formula_injection_protection(client: TestClient):
    assert sanitize_csv_cell("=CMD|' /C calc'!A0") == "'=CMD|' /C calc'!A0"
    assert sanitize_csv_cell("+SUM(A1:A10)") == "'+SUM(A1:A10)"
    assert sanitize_csv_cell("-100+200") == "'-100+200"
    assert sanitize_csv_cell("@IMPORTXML()") == "'@IMPORTXML()"
    assert sanitize_csv_cell("Normal Customer Name") == "Normal Customer Name"

    # Also test end-to-end CSV export with a malicious customer name
    malicious_csv = _make_csv_bytes(
        ["Name", "Created at", "Customer Name", "Customer Phone", "Shipping City", "Total", "Payment Method", "Fulfillment Status"],
        [["#09999", "18/10/2026 10:00", "=HYPERLINK(\"http://evil.example\")", "9811199999", "Mumbai", "999.00", "Prepaid", "unfulfilled"]],
    )
    client.post(
        "/api/import/commit",
        files={"file": ("malicious.csv", malicious_csv, "text/csv")},
        data={"source_type": "shopify_orders", "date_format_preference": "DD/MM/YYYY"},
    )
    export_resp = client.get("/api/orders/export.csv")
    assert export_resp.status_code == 200
    assert "'=HYPERLINK" in export_resp.text


def test_access_control_and_workspace_isolation(client: TestClient, monkeypatch):
    # Enable strict authentication requirement and Supabase JWT secret
    monkeypatch.setenv("REQUIRE_AUTH", "true")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "super-secret-test-jwt-key-32-bytes-long")

    # Unauthenticated request is rejected with 401
    unauth_resp = client.get("/api/overview")
    assert unauth_resp.status_code == 401

    # Create two distinct user JWT tokens (User A and User B)
    token_a = jwt.encode(
        {"sub": "user-aaa-111", "email": "ops_a@brand.in"},
        "super-secret-test-jwt-key-32-bytes-long",
        algorithm="HS256",
    )
    token_b = jwt.encode(
        {"sub": "user-bbb-222", "email": "ops_b@brand.in"},
        "super-secret-test-jwt-key-32-bytes-long",
        algorithm="HS256",
    )

    # User A uploads an order
    csv_a = _make_csv_bytes(
        ["Name", "Created at", "Customer Name", "Customer Phone", "Shipping City", "Total"],
        [["#A100", "18/10/2026 10:00", "Tenant A Customer", "9810000001", "Delhi", "1500.00"]],
    )
    up_a = client.post(
        "/api/import/commit",
        headers={"Authorization": f"Bearer {token_a}"},
        files={"file": ("tenant_a.csv", csv_a, "text/csv")},
        data={"source_type": "shopify_orders", "date_format_preference": "DD/MM/YYYY"},
    )
    assert up_a.status_code == 200

    # User A sees 1 order
    orders_a = client.get("/api/orders", headers={"Authorization": f"Bearer {token_a}"}).json()
    assert orders_a["total"] == 1
    assert orders_a["items"][0]["order_number"] == "#A100"

    # User B is completely isolated and sees 0 orders!
    orders_b = client.get("/api/orders", headers={"Authorization": f"Bearer {token_b}"}).json()
    assert orders_b["total"] == 0


def test_founder_dashboard_cod_remittance_and_sku_runway(client: TestClient):
    client.post("/api/demo/load")

    resp = client.get("/api/founder/dashboard")
    assert resp.status_code == 200
    data = resp.json()

    ue = data["unit_economics"]
    assert ue["active_gmv_paise"] > 0
    assert ue["net_contribution_paise"] > 0
    assert ue["overdue_cod_count"] > 0
    assert ue["disputed_cod_count"] > 0
    assert ue["rto_margin_bleed_paise"] > 0

    # COD Remittance Ledger
    ledger = data["cod_remittance_ledger"]
    assert len(ledger) > 20
    statuses = {r["remittance_status"] for r in ledger}
    assert "OVERDUE_PAYOUT" in statuses
    assert "SHORT_REMITTED" in statuses
    assert "SETTLED" in statuses

    # SKU Inventory Runway
    skus = data["sku_inventory_runway"]
    assert len(skus) == 8
    assert any(s["stock_status"] == "CRITICAL_STOCKOUT_RISK" for s in skus)

    # Courier SLA Scorecard
    couriers = data["courier_scorecard"]
    assert len(couriers) >= 5
    assert all("sla_score" in c and "recommendation" in c for c in couriers)

    # COD Export CSV
    csv_resp = client.get("/api/founder/cod-export.csv?status_filter=OVERDUE_PAYOUT")
    assert csv_resp.status_code == 200
    assert "Expected Net Payout (INR)" in csv_resp.text


def test_mcp_jsonrpc_server_and_studio_playground(client: TestClient):
    client.post("/api/demo/load")

    # 1. MCP Catalog endpoint
    cat = client.get("/api/mcp/catalog").json()
    assert cat["protocol_version"] == "2024-11-05"
    assert len(cat["tools"]) == 8
    assert len(cat["resources"]) == 4
    assert len(cat["prompts"]) == 3

    # 2. JSON-RPC 2.0 initialize
    init_resp = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    ).json()
    assert init_resp["result"]["protocolVersion"] == "2024-11-05"
    assert init_resp["result"]["serverInfo"]["name"] == "order-ops-founder-mcp"

    # 3. JSON-RPC 2.0 tools/list
    tools_resp = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ).json()
    tool_names = {t["name"] for t in tools_resp["result"]["tools"]}
    assert "get_founder_daily_brief" in tool_names
    assert "audit_cod_remittances" in tool_names
    assert "inspect_order_provenance" in tool_names

    # 4. JSON-RPC 2.0 tools/call -> audit_cod_remittances
    call_resp = client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "audit_cod_remittances",
                "arguments": {"status_filter": "OVERDUE_PAYOUT", "limit": 5},
            },
        },
    ).json()
    assert call_resp["result"]["isError"] is False
    structured = call_resp["result"]["structuredContent"]
    assert structured["total_matching_cod_orders"] > 0
    assert len(structured["rows"]) <= 5

    # 5. JSON-RPC 2.0 tools/call -> inspect_order_provenance (#01004)
    prov_resp = client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "inspect_order_provenance",
                "arguments": {"order_id": "#01004"},
            },
        },
    ).json()
    assert prov_resp["result"]["isError"] is False
    assert prov_resp["result"]["structuredContent"]["order"]["order_number"] == "#01004"

    # 6. MCP Studio Playground endpoint (/api/mcp/execute)
    studio_resp = client.post(
        "/api/mcp/execute",
        json={
            "kind": "resource",
            "name_or_uri": "orderops://founder/daily-brief",
            "arguments": {},
        },
    ).json()
    assert studio_resp["jsonrpc_request"]["method"] == "resources/read"
    assert "contents" in studio_resp["jsonrpc_response"]["result"]

