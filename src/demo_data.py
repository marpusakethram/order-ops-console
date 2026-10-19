"""Deterministic synthetic demo dataset generator (fixed seed) for Order Ops Console.

Generates ~240 realistic Indian D2C orders across Shopify Orders CSV, Courier Tracking CSV,
and WhatsApp Complaints CSV, anchored to the demo reference time:
  2026-10-19 18:00:00 IST (Asia/Kolkata)

Includes explicit showcase scenarios covering every reconciliation and operational rule.
All names, phones, and addresses are fictional and synthetic.
"""

from __future__ import annotations

import csv
import io
import random
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from src.attention_rules import get_or_create_workspace_settings
from src.config import BASE_DIR, DEFAULT_DEMO_REFERENCE_ISO
from src.importer import ingest_csv_bytes, reset_workspace_data

DEMO_SEED = 20261019

PRODUCTS = [
    ("SKU-KUR-01", "Indigo Handblock Cotton Kurta", "1499.00"),
    ("SKU-SRM-02", "Kumkumadi Glow Facial Serum 30ml", "899.00"),
    ("SKU-BRS-03", "Filter Kaapi Brass Dabara Set", "1299.00"),
    ("SKU-LIN-04", "Mulmul Dohar Blanket - Jaipur Floral", "2499.00"),
    ("SKU-TEA-05", "Assam Single Estate Masala Chai Tin", "599.00"),
    ("SKU-BAG-06", "Kalamkari Canvas Tote Bag", "799.00"),
    ("SKU-OIL-07", "Cold-Pressed Bhringraj Hair Oil 200ml", "649.00"),
    ("SKU-ank-08", "Handcrafted Silver Oxidised Jhumkas", "1199.00"),
]

CITIES = [
    ("Bengaluru", "Karnataka", "560034", "Bengaluru Peenya Hub"),
    ("Mumbai", "Maharashtra", "400050", "Bhiwandi Mega Hub"),
    ("New Delhi", "Delhi", "110016", "Okhla Phase II Hub"),
    ("Hyderabad", "Telangana", "500081", "Shamshabad Sort Facility"),
    ("Pune", "Maharashtra", "411038", "Chakan Logistics Park"),
    ("Chennai", "Tamil Nadu", "600040", "Sriperumbudur Hub"),
    ("Jaipur", "Rajasthan", "302017", "Sitapura Industrial Hub"),
    ("Ahmedabad", "Gujarat", "380015", "Sanand Cross-Dock"),
    ("Kochi", "Kerala", "682020", "Aluva Transit Center"),
    ("Indore", "Madhya Pradesh", "452010", "Dewas Naka Hub"),
    ("Lucknow", "Uttar Pradesh", "226010", "Transport Nagar Hub"),
    ("Coimbatore", "Tamil Nadu", "641012", "Peelamedu Hub"),
]

COURIERS = [
    ("Delhivery", "DLV"),
    ("BlueDart", "BLD"),
    ("XpressBees", "XPB"),
    ("Ecom Express", "ECM"),
    ("DTDC", "DTC"),
    ("Shadowfax", "SFX"),
]

FIRST_NAMES = [
    "Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Reyansh", "Siddharth", "Karthik",
    "Rohan", "Vikram", "Nikhil", "Pranav", "Ananya", "Diya", "Saanvi", "Aadhya",
    "Kavya", "Meera", "Ishita", "Neha", "Pooja", "Sneha", "Riya", "Tanvi",
    "Divya", "Shruti", "Nandini", "swati", "Harsh", "Gaurav", "Manish", "Deepak",
]

LAST_NAMES = [
    "Sharma", "Verma", "Nair", "Iyer", "Menon", "Kulkarni", "Deshmukh", "Patel",
    "Reddy", "Rao", "Chatterjee", "Banerjee", "Gupta", "Malhotra", "Kapoor", "Joshi",
    "Mehta", "Agarwal", "Bhatia", "Shetty", "Hegde", "Pillai", "Chauhan", "Sinha",
]


def _dict_rows_to_csv_bytes(headers: list[str], rows: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return buf.getvalue().encode("utf-8")


def generate_demo_csv_bundles() -> dict[str, bytes]:
    """Deterministically generate Shopify, Courier, and WhatsApp CSV files with 240 unique orders."""
    rng = random.Random(DEMO_SEED)

    shopify_headers = [
        "Name",
        "Created at",
        "Customer Name",
        "Customer Phone",
        "Email",
        "Shipping City",
        "Shipping Province",
        "Shipping Zip",
        "Total",
        "Payment Method",
        "Financial Status",
        "Fulfillment Status",
        "Cancelled at",
        "Lineitem SKU",
        "Lineitem name",
        "Lineitem quantity",
        "Lineitem price",
        "Tracking Number",
        "Tracking Company",
        "Promised Delivery Date",
    ]

    courier_headers = [
        "AWB Number",
        "Courier Name",
        "Client Order ID",
        "Shipment Status",
        "Scan Timestamp",
        "Current Hub",
        "Consignee Phone",
        "Consignee Name",
        "Collectable Value",
        "Expected Delivery Date",
        "Delivery Attempts",
        "Status Remarks",
    ]

    whatsapp_headers = [
        "Ticket ID",
        "Received At",
        "WhatsApp Number",
        "Sender Name",
        "Order Ref",
        "AWB",
        "Issue Type",
        "Message Body",
        "Ticket Status",
    ]

    shopify_rows: list[dict[str, Any]] = []
    courier_rows: list[dict[str, Any]] = []
    whatsapp_rows: list[dict[str, Any]] = []

    # =========================================================================
    # PART 1: 22 Explicit Showcase Orders (#01001 to #01022)
    # Note leading zero in order numbers ("#01001") to demonstrate string preservation!
    # =========================================================================

    # 1. #01001: Delivered order (clean, no flags)
    shopify_rows.append({
        "Name": "#01001",
        "Created at": "12/10/2026 10:15",
        "Customer Name": "Aditi Rao",
        "Customer Phone": "+91 98101-10001",
        "Email": "aditi.rao@example.in",
        "Shipping City": "Bengaluru",
        "Shipping Province": "Karnataka",
        "Shipping Zip": "560034",
        "Total": "₹1,499.00",
        "Payment Method": "Prepaid (UPI)",
        "Financial Status": "paid",
        "Fulfillment Status": "fulfilled",
        "Cancelled at": "",
        "Lineitem SKU": "SKU-KUR-01",
        "Lineitem name": "Indigo Handblock Cotton Kurta",
        "Lineitem quantity": "1",
        "Lineitem price": "1499.00",
        "Tracking Number": "00BLD10001",
        "Tracking Company": "BlueDart",
        "Promised Delivery Date": "16/10/2026 18:00",
    })
    courier_rows.extend([
        {
            "AWB Number": "00BLD10001",
            "Courier Name": "BlueDart",
            "Client Order ID": "ORD-01001",
            "Shipment Status": "Picked Up",
            "Scan Timestamp": "12/10/2026 17:30",
            "Current Hub": "Bengaluru Origin Hub",
            "Consignee Phone": "09810110001",
            "Consignee Name": "Aditi Rao",
            "Collectable Value": "1499.00",
            "Expected Delivery Date": "16/10/2026 18:00",
            "Delivery Attempts": "0",
            "Status Remarks": "Bagged at origin",
        },
        {
            "AWB Number": "00BLD10001",
            "Courier Name": "BlueDart",
            "Client Order ID": "ORD-01001",
            "Shipment Status": "Delivered",
            "Scan Timestamp": "15/10/2026 14:20",
            "Current Hub": "Bengaluru Indiranagar",
            "Consignee Phone": "09810110001",
            "Consignee Name": "Aditi Rao",
            "Collectable Value": "1499.00",
            "Expected Delivery Date": "16/10/2026 18:00",
            "Delivery Attempts": "1",
            "Status Remarks": "Delivered to consignee with OTP",
        },
    ])

    # 2. #01002: Newly placed order (placed 7 hours before reference time -> should NOT be flagged)
    shopify_rows.append({
        "Name": "#01002",
        "Created at": "19/10/2026 11:00",
        "Customer Name": "Karthik Nair",
        "Customer Phone": "+91 98101 10002",
        "Email": "karthik.nair@example.in",
        "Shipping City": "Kochi",
        "Shipping Province": "Kerala",
        "Shipping Zip": "682020",
        "Total": "899.00",
        "Payment Method": "Prepaid",
        "Financial Status": "paid",
        "Fulfillment Status": "unfulfilled",
        "Cancelled at": "",
        "Lineitem SKU": "SKU-SRM-02",
        "Lineitem name": "Kumkumadi Glow Facial Serum 30ml",
        "Lineitem quantity": "1",
        "Lineitem price": "899.00",
        "Tracking Number": "",
        "Tracking Company": "",
        "Promised Delivery Date": "24/10/2026 18:00",
    })

    # 3. #01003: Old unshipped order (placed 5 days ago, paid Prepaid -> Flagged High UNSHIPPED_DELAY)
    shopify_rows.append({
        "Name": "#01003",
        "Created at": "14/10/2026 15:00",
        "Customer Name": "Meera Iyer",
        "Customer Phone": "+91 98101-10003",
        "Email": "meera.iyer@example.in",
        "Shipping City": "Chennai",
        "Shipping Province": "Tamil Nadu",
        "Shipping Zip": "600040",
        "Total": "₹2,499.00",
        "Payment Method": "Prepaid",
        "Financial Status": "paid",
        "Fulfillment Status": "unfulfilled",
        "Cancelled at": "",
        "Lineitem SKU": "SKU-LIN-04",
        "Lineitem name": "Mulmul Dohar Blanket - Jaipur Floral",
        "Lineitem quantity": "1",
        "Lineitem price": "2499.00",
        "Tracking Number": "",
        "Tracking Company": "",
        "Promised Delivery Date": "19/10/2026 18:00",
    })

    # 4. #01004: Stale in-transit shipment + WhatsApp complaint with order number
    shopify_rows.append({
        "Name": "#01004",
        "Created at": "13/10/2026 12:00",
        "Customer Name": "Vikram Malhotra",
        "Customer Phone": "+91 (0) 98101 10004",
        "Email": "vikram.m@example.in",
        "Shipping City": "Mumbai",
        "Shipping Province": "Maharashtra",
        "Shipping Zip": "400050",
        "Total": "₹1,299.00",
        "Payment Method": "Prepaid",
        "Financial Status": "paid",
        "Fulfillment Status": "fulfilled",
        "Cancelled at": "",
        "Lineitem SKU": "SKU-BRS-03",
        "Lineitem name": "Filter Kaapi Brass Dabara Set",
        "Lineitem quantity": "1",
        "Lineitem price": "1299.00",
        "Tracking Number": "DLV0098401",
        "Tracking Company": "Delhivery",
        "Promised Delivery Date": "18/10/2026 18:00",
    })
    courier_rows.extend([
        {
            "AWB Number": "DLV0098401",
            "Courier Name": "Delhivery",
            "Client Order ID": "#01004",
            "Shipment Status": "In Transit - Hub",
            "Scan Timestamp": "15/10/2026 06:00",
            "Current Hub": "Bhiwandi Mega Hub",
            "Consignee Phone": "919810110004",
            "Consignee Name": "Vikram Malhotra",
            "Collectable Value": "1299.00",
            "Expected Delivery Date": "18/10/2026 18:00",
            "Delivery Attempts": "0",
            "Status Remarks": "Arrived at Bhiwandi hub; awaiting linehaul connection",
        },
        # Repeated identical courier event row to demonstrate deduplication / idempotency
        {
            "AWB Number": "DLV0098401",
            "Courier Name": "Delhivery",
            "Client Order ID": "#01004",
            "Shipment Status": "In Transit - Hub",
            "Scan Timestamp": "15/10/2026 06:00",
            "Current Hub": "Bhiwandi Mega Hub",
            "Consignee Phone": "919810110004",
            "Consignee Name": "Vikram Malhotra",
            "Collectable Value": "1299.00",
            "Expected Delivery Date": "18/10/2026 18:00",
            "Delivery Attempts": "0",
            "Status Remarks": "Arrived at Bhiwandi hub; awaiting linehaul connection",
        },
    ])
    whatsapp_rows.append({
        "Ticket ID": "WA-1001",
        "Received At": "19/10/2026 11:20",
        "WhatsApp Number": "+91 98101 10004",
        "Sender Name": "Vikram Malhotra",
        "Order Ref": "",
        "AWB": "",
        "Issue Type": "Stuck in Transit",
        "Message Body": "Hi team, my order #01004 has had no tracking update since reaching Bhiwandi hub 4 days ago. When will it be delivered?",
        "Ticket Status": "Open",
    })

    # 5. #01005: COD order with 2 Failed delivery attempts + complaint without order number (matched via phone + name)
    shopify_rows.append({
        "Name": "#01005",
        "Created at": "14/10/2026 09:30",
        "Customer Name": "Sneha Deshmukh",
        "Customer Phone": "+91 98220-33445",
        "Email": "sneha.d@example.in",
        "Shipping City": "Pune",
        "Shipping Province": "Maharashtra",
        "Shipping Zip": "411038",
        "Total": "₹1,899.00",
        "Payment Method": "Cash on Delivery (COD)",
        "Financial Status": "pending",
        "Fulfillment Status": "fulfilled",
        "Cancelled at": "",
        "Lineitem SKU": "SKU-KUR-01",
        "Lineitem name": "Indigo Handblock Cotton Kurta",
        "Lineitem quantity": "1",
        "Lineitem price": "1899.00",
        "Tracking Number": "",
        "Tracking Company": "XpressBees",
        "Promised Delivery Date": "18/10/2026 18:00",
    })
    courier_rows.extend([
        {
            "AWB Number": "XPB0077201",
            "Courier Name": "XpressBees",
            "Client Order ID": "01005",
            "Shipment Status": "Failed Attempt - Customer Unavailable",
            "Scan Timestamp": "18/10/2026 15:10",
            "Current Hub": "Pune Kothrud Hub",
            "Consignee Phone": "09822033445",
            "Consignee Name": "Sneha Deshmukh",
            "Collectable Value": "1899.00",
            "Expected Delivery Date": "18/10/2026 18:00",
            "Delivery Attempts": "1",
            "Status Remarks": "Consignee not reachable on call",
        },
        {
            "AWB Number": "XPB0077201",
            "Courier Name": "XpressBees",
            "Client Order ID": "01005",
            "Shipment Status": "Undelivered - Door Locked",
            "Scan Timestamp": "19/10/2026 13:45",
            "Current Hub": "Pune Kothrud Hub",
            "Consignee Phone": "09822033445",
            "Consignee Name": "Sneha Deshmukh",
            "Collectable Value": "1899.00",
            "Expected Delivery Date": "18/10/2026 18:00",
            "Delivery Attempts": "2",
            "Status Remarks": "2nd NDR: House locked according to rider",
        },
    ])
    whatsapp_rows.append({
        "Ticket ID": "WA-1002",
        "Received At": "19/10/2026 14:30",
        "WhatsApp Number": "9822033445",
        "Sender Name": "Sneha Deshmukh",
        "Order Ref": "",
        "AWB": "",
        "Issue Type": "Fake Delivery Attempt",
        "Message Body": "Hello, I got an SMS saying door locked in Pune today, but I was home all afternoon and nobody called me! Please arrange re-delivery.",
        "Ticket Status": "Open",
    })

    # 6. #01006: Return-to-Origin (RTO) initiated
    shopify_rows.append({
        "Name": "#01006",
        "Created at": "11/10/2026 16:40",
        "Customer Name": "Rohan Verma",
        "Customer Phone": "+91 98101 10006",
        "Email": "rohan.v@example.in",
        "Shipping City": "Lucknow",
        "Shipping Province": "Uttar Pradesh",
        "Shipping Zip": "226010",
        "Total": "₹1,499.00",
        "Payment Method": "COD",
        "Financial Status": "pending",
        "Fulfillment Status": "fulfilled",
        "Cancelled at": "",
        "Lineitem SKU": "SKU-KUR-01",
        "Lineitem name": "Indigo Handblock Cotton Kurta",
        "Lineitem quantity": "1",
        "Lineitem price": "1499.00",
        "Tracking Number": "ECM0065101",
        "Tracking Company": "Ecom Express",
        "Promised Delivery Date": "16/10/2026 18:00",
    })
    courier_rows.append({
        "AWB Number": "ECM0065101",
        "Courier Name": "Ecom Express",
        "Client Order ID": "ORD-01006",
        "Shipment Status": "RTO Initiated - Returning to Origin",
        "Scan Timestamp": "18/10/2026 19:00",
        "Current Hub": "Lucknow Transport Nagar Hub",
        "Consignee Phone": "+919810110006",
        "Consignee Name": "Rohan Verma",
        "Collectable Value": "1499.00",
        "Expected Delivery Date": "16/10/2026 18:00",
        "Delivery Attempts": "3",
        "Status Remarks": "Max 3 delivery attempts exhausted; returning to warehouse",
    })

    # 7. #01007: Cancelled order (placed 6 days ago, cancelled -> must NOT be flagged as awaiting shipment!)
    shopify_rows.append({
        "Name": "#01007",
        "Created at": "13/10/2026 10:00",
        "Customer Name": "Pooja Gupta",
        "Customer Phone": "+91 98101 10007",
        "Email": "pooja.g@example.in",
        "Shipping City": "Jaipur",
        "Shipping Province": "Rajasthan",
        "Shipping Zip": "302017",
        "Total": "₹999.00",
        "Payment Method": "COD",
        "Financial Status": "voided",
        "Fulfillment Status": "cancelled",
        "Cancelled at": "13/10/2026 12:15",
        "Lineitem SKU": "SKU-BAG-06",
        "Lineitem name": "Kalamkari Canvas Tote Bag",
        "Lineitem quantity": "1",
        "Lineitem price": "999.00",
        "Tracking Number": "",
        "Tracking Company": "",
        "Promised Delivery Date": "",
    })

    # 8. #01008: Partial fulfillment (2 line items, 1 fulfilled, 1 unfulfilled)
    shopify_rows.extend([
        {
            "Name": "#01008",
            "Created at": "16/10/2026 14:10",
            "Customer Name": "Siddharth Joshi",
            "Customer Phone": "+91 98101 10008",
            "Email": "siddharth.j@example.in",
            "Shipping City": "Hyderabad",
            "Shipping Province": "Telangana",
            "Shipping Zip": "500081",
            "Total": "₹2,098.00",
            "Payment Method": "Prepaid",
            "Financial Status": "paid",
            "Fulfillment Status": "fulfilled",
            "Cancelled at": "",
            "Lineitem SKU": "SKU-KUR-01",
            "Lineitem name": "Indigo Handblock Cotton Kurta",
            "Lineitem quantity": "1",
            "Lineitem price": "1499.00",
            "Tracking Number": "DLV0080801",
            "Tracking Company": "Delhivery",
            "Promised Delivery Date": "21/10/2026 18:00",
        },
        {
            "Name": "#01008",
            "Created at": "16/10/2026 14:10",
            "Customer Name": "Siddharth Joshi",
            "Customer Phone": "+91 98101 10008",
            "Email": "siddharth.j@example.in",
            "Shipping City": "Hyderabad",
            "Shipping Province": "Telangana",
            "Shipping Zip": "500081",
            "Total": "₹2,098.00",
            "Payment Method": "Prepaid",
            "Financial Status": "paid",
            "Fulfillment Status": "unfulfilled",
            "Cancelled at": "",
            "Lineitem SKU": "SKU-TEA-05",
            "Lineitem name": "Assam Single Estate Masala Chai Tin",
            "Lineitem quantity": "1",
            "Lineitem price": "599.00",
            "Tracking Number": "",
            "Tracking Company": "",
            "Promised Delivery Date": "21/10/2026 18:00",
        },
    ])
    courier_rows.append({
        "AWB Number": "DLV0080801",
        "Courier Name": "Delhivery",
        "Client Order ID": "ORD-01008",
        "Shipment Status": "In Transit",
        "Scan Timestamp": "18/10/2026 20:15",
        "Current Hub": "Shamshabad Sort Facility",
        "Consignee Phone": "9810110008",
        "Consignee Name": "Siddharth Joshi",
        "Collectable Value": "2098.00",
        "Expected Delivery Date": "21/10/2026 18:00",
        "Delivery Attempts": "0",
        "Status Remarks": "Part 1 of 2 in transit",
    })

    # 9. #01009: Multiple shipments for one order (2 AWBs: 1 Delivered, 1 Stale In-Transit)
    shopify_rows.extend([
        {
            "Name": "#01009",
            "Created at": "13/10/2026 11:00",
            "Customer Name": "Nandini Chatterjee",
            "Customer Phone": "+91 98101 10009",
            "Email": "nandini.c@example.in",
            "Shipping City": "New Delhi",
            "Shipping Province": "Delhi",
            "Shipping Zip": "110016",
            "Total": "₹3,798.00",
            "Payment Method": "Prepaid",
            "Financial Status": "paid",
            "Fulfillment Status": "fulfilled",
            "Cancelled at": "",
            "Lineitem SKU": "SKU-LIN-04",
            "Lineitem name": "Mulmul Dohar Blanket - Jaipur Floral",
            "Lineitem quantity": "1",
            "Lineitem price": "2499.00",
            "Tracking Number": "BLD0055101",
            "Tracking Company": "BlueDart",
            "Promised Delivery Date": "18/10/2026 18:00",
        },
        {
            "Name": "#01009",
            "Created at": "13/10/2026 11:00",
            "Customer Name": "Nandini Chatterjee",
            "Customer Phone": "+91 98101 10009",
            "Email": "nandini.c@example.in",
            "Shipping City": "New Delhi",
            "Shipping Province": "Delhi",
            "Shipping Zip": "110016",
            "Total": "₹3,798.00",
            "Payment Method": "Prepaid",
            "Financial Status": "paid",
            "Fulfillment Status": "fulfilled",
            "Cancelled at": "",
            "Lineitem SKU": "SKU-BRS-03",
            "Lineitem name": "Filter Kaapi Brass Dabara Set",
            "Lineitem quantity": "1",
            "Lineitem price": "1299.00",
            "Tracking Number": "DLV0055102",
            "Tracking Company": "Delhivery",
            "Promised Delivery Date": "18/10/2026 18:00",
        },
    ])
    courier_rows.extend([
        {
            "AWB Number": "BLD0055101",
            "Courier Name": "BlueDart",
            "Client Order ID": "#01009",
            "Shipment Status": "Delivered",
            "Scan Timestamp": "16/10/2026 12:30",
            "Current Hub": "New Delhi Hauz Khas",
            "Consignee Phone": "9810110009",
            "Consignee Name": "Nandini Chatterjee",
            "Collectable Value": "2499.00",
            "Expected Delivery Date": "18/10/2026 18:00",
            "Delivery Attempts": "1",
            "Status Remarks": "Box 1 of 2 delivered",
        },
        {
            "AWB Number": "DLV0055102",
            "Courier Name": "Delhivery",
            "Client Order ID": "#01009",
            "Shipment Status": "In Transit",
            "Scan Timestamp": "15/10/2026 09:00",
            "Current Hub": "Okhla Phase II Hub",
            "Consignee Phone": "9810110009",
            "Consignee Name": "Nandini Chatterjee",
            "Collectable Value": "1299.00",
            "Expected Delivery Date": "18/10/2026 18:00",
            "Delivery Attempts": "0",
            "Status Remarks": "Box 2 of 2 awaiting dispatch from hub",
        },
    ])

    # 10. #01010: Duplicate Shopify line items (2 distinct items + 1 exact duplicate row)
    row_1010_item1 = {
        "Name": "#01010",
        "Created at": "17/10/2026 16:00",
        "Customer Name": "Pranav Hegde",
        "Customer Phone": "+91 98101 10010",
        "Email": "pranav.h@example.in",
        "Shipping City": "Bengaluru",
        "Shipping Province": "Karnataka",
        "Shipping Zip": "560034",
        "Total": "₹1,548.00",
        "Payment Method": "Prepaid",
        "Financial Status": "paid",
        "Fulfillment Status": "fulfilled",
        "Cancelled at": "",
        "Lineitem SKU": "SKU-SRM-02",
        "Lineitem name": "Kumkumadi Glow Facial Serum 30ml",
        "Lineitem quantity": "1",
        "Lineitem price": "899.00",
        "Tracking Number": "SFX0010101",
        "Tracking Company": "Shadowfax",
        "Promised Delivery Date": "21/10/2026 18:00",
    }
    row_1010_item2 = {
        "Name": "#01010",
        "Created at": "17/10/2026 16:00",
        "Customer Name": "Pranav Hegde",
        "Customer Phone": "+91 98101 10010",
        "Email": "pranav.h@example.in",
        "Shipping City": "Bengaluru",
        "Shipping Province": "Karnataka",
        "Shipping Zip": "560034",
        "Total": "₹1,548.00",
        "Payment Method": "Prepaid",
        "Financial Status": "paid",
        "Fulfillment Status": "fulfilled",
        "Cancelled at": "",
        "Lineitem SKU": "SKU-OIL-07",
        "Lineitem name": "Cold-Pressed Bhringraj Hair Oil 200ml",
        "Lineitem quantity": "1",
        "Lineitem price": "649.00",
        "Tracking Number": "SFX0010101",
        "Tracking Company": "Shadowfax",
        "Promised Delivery Date": "21/10/2026 18:00",
    }
    shopify_rows.extend([row_1010_item1, row_1010_item2, dict(row_1010_item1)])  # 3rd row is an exact duplicate!
    courier_rows.append({
        "AWB Number": "SFX0010101",
        "Courier Name": "Shadowfax",
        "Client Order ID": "01010",
        "Shipment Status": "Out for Delivery",
        "Scan Timestamp": "19/10/2026 10:00",
        "Current Hub": "Bengaluru Koramangala",
        "Consignee Phone": "9810110010",
        "Consignee Name": "Pranav Hegde",
        "Collectable Value": "1548.00",
        "Expected Delivery Date": "21/10/2026 18:00",
        "Delivery Attempts": "0",
        "Status Remarks": "Out with delivery executive",
    })

    # 11 & 12. #01011 & #01012: Two orders sharing a household phone number (+91 98200 11223)
    shopify_rows.extend([
        {
            "Name": "#01011",
            "Created at": "15/10/2026 11:00",
            "Customer Name": "Kavya Sharma",
            "Customer Phone": "+91 98200 11223",
            "Email": "kavya.sharma@example.in",
            "Shipping City": "Mumbai",
            "Shipping Province": "Maharashtra",
            "Shipping Zip": "400050",
            "Total": "₹1,499.00",
            "Payment Method": "Prepaid",
            "Financial Status": "paid",
            "Fulfillment Status": "fulfilled",
            "Cancelled at": "",
            "Lineitem SKU": "SKU-KUR-01",
            "Lineitem name": "Indigo Handblock Cotton Kurta",
            "Lineitem quantity": "1",
            "Lineitem price": "1499.00",
            "Tracking Number": "DLV0010111",
            "Tracking Company": "Delhivery",
            "Promised Delivery Date": "20/10/2026 18:00",
        },
        {
            "Name": "#01012",
            "Created at": "17/10/2026 18:30",
            "Customer Name": "Arjun Sharma",
            "Customer Phone": "09820011223",
            "Email": "arjun.sharma@example.in",
            "Shipping City": "Mumbai",
            "Shipping Province": "Maharashtra",
            "Shipping Zip": "400050",
            "Total": "₹1,299.00",
            "Payment Method": "COD",
            "Financial Status": "pending",
            "Fulfillment Status": "fulfilled",
            "Cancelled at": "",
            "Lineitem SKU": "SKU-BRS-03",
            "Lineitem name": "Filter Kaapi Brass Dabara Set",
            "Lineitem quantity": "1",
            "Lineitem price": "1299.00",
            "Tracking Number": "BLD0010121",
            "Tracking Company": "BlueDart",
            "Promised Delivery Date": "21/10/2026 18:00",
        },
    ])
    courier_rows.extend([
        {
            "AWB Number": "DLV0010111",
            "Courier Name": "Delhivery",
            "Client Order ID": "01011",
            "Shipment Status": "In Transit",
            "Scan Timestamp": "18/10/2026 16:00",
            "Current Hub": "Bhiwandi Mega Hub",
            "Consignee Phone": "9820011223",
            "Consignee Name": "Kavya Sharma",
            "Collectable Value": "1499.00",
            "Expected Delivery Date": "20/10/2026 18:00",
            "Delivery Attempts": "0",
            "Status Remarks": "In transit",
        },
        {
            "AWB Number": "BLD0010121",
            "Courier Name": "BlueDart",
            "Client Order ID": "01012",
            "Shipment Status": "In Transit",
            "Scan Timestamp": "18/10/2026 21:00",
            "Current Hub": "Mumbai Santacruz Hub",
            "Consignee Phone": "9820011223",
            "Consignee Name": "Arjun Sharma",
            "Collectable Value": "1299.00",
            "Expected Delivery Date": "21/10/2026 18:00",
            "Delivery Attempts": "0",
            "Status Remarks": "In transit",
        },
    ])
    # Ambiguous WhatsApp complaint from the shared household phone without an Order ID!
    whatsapp_rows.append({
        "Ticket ID": "WA-1003",
        "Received At": "19/10/2026 12:15",
        "WhatsApp Number": "+91-98200-11223",
        "Sender Name": "Sharma Residence",
        "Order Ref": "",
        "AWB": "",
        "Issue Type": "Address Change Request",
        "Message Body": "Hi, we placed two orders from this number for Bandra Mumbai. Can you ask the delivery person to call on arrival?",
        "Ticket Status": "Open",
    })

    # 13 & 14. #01013 & #01014: Two different customers sharing the exact same name ("Rahul Kulkarni")
    shopify_rows.extend([
        {
            "Name": "#01013",
            "Created at": "15/10/2026 09:00",
            "Customer Name": "Rahul Kulkarni",
            "Customer Phone": "+91 98300 44551",
            "Email": "rahul.k.pune@example.in",
            "Shipping City": "Pune",
            "Shipping Province": "Maharashtra",
            "Shipping Zip": "411038",
            "Total": "₹1,299.00",
            "Payment Method": "Prepaid",
            "Financial Status": "paid",
            "Fulfillment Status": "fulfilled",
            "Cancelled at": "",
            "Lineitem SKU": "SKU-BRS-03",
            "Lineitem name": "Filter Kaapi Brass Dabara Set",
            "Lineitem quantity": "1",
            "Lineitem price": "1299.00",
            "Tracking Number": "",
            "Tracking Company": "DTDC",
            "Promised Delivery Date": "20/10/2026 18:00",
        },
        {
            "Name": "#01014",
            "Created at": "16/10/2026 10:30",
            "Customer Name": "Rahul Kulkarni",
            "Customer Phone": "+91 98300 99882",
            "Email": "rahul.k.blr@example.in",
            "Shipping City": "Bengaluru",
            "Shipping Province": "Karnataka",
            "Shipping Zip": "560034",
            "Total": "₹1,299.00",
            "Payment Method": "COD",
            "Financial Status": "pending",
            "Fulfillment Status": "fulfilled",
            "Cancelled at": "",
            "Lineitem SKU": "SKU-BRS-03",
            "Lineitem name": "Filter Kaapi Brass Dabara Set",
            "Lineitem quantity": "1",
            "Lineitem price": "1299.00",
            "Tracking Number": "",
            "Tracking Company": "DTDC",
            "Promised Delivery Date": "21/10/2026 18:00",
        },
    ])
    # Courier row with only customer name "Rahul Kulkarni" and amount 1299.00 (no Order ID, no Phone) -> Must NOT auto-match!
    courier_rows.append({
        "AWB Number": "DTC0099113",
        "Courier Name": "DTDC",
        "Client Order ID": "",
        "Shipment Status": "In Transit",
        "Scan Timestamp": "18/10/2026 14:00",
        "Current Hub": "Pune Chakan Hub",
        "Consignee Phone": "",
        "Consignee Name": "Rahul Kulkarni",
        "Collectable Value": "1299.00",
        "Expected Delivery Date": "20/10/2026 18:00",
        "Delivery Attempts": "0",
        "Status Remarks": "Manual manifest entry missing client order ID and phone",
    })

    # 15. #01015: "Delivered but not received" complaint contradiction
    shopify_rows.append({
        "Name": "#01015",
        "Created at": "12/10/2026 14:00",
        "Customer Name": "Ananya Menon",
        "Customer Phone": "+91 98450 22334",
        "Email": "ananya.menon@example.in",
        "Shipping City": "Bengaluru",
        "Shipping Province": "Karnataka",
        "Shipping Zip": "560034",
        "Total": "₹2,499.00",
        "Payment Method": "Prepaid",
        "Financial Status": "paid",
        "Fulfillment Status": "fulfilled",
        "Cancelled at": "",
        "Lineitem SKU": "SKU-LIN-04",
        "Lineitem name": "Mulmul Dohar Blanket - Jaipur Floral",
        "Lineitem quantity": "1",
        "Lineitem price": "2499.00",
        "Tracking Number": "BLD0088301",
        "Tracking Company": "BlueDart",
        "Promised Delivery Date": "17/10/2026 18:00",
    })
    courier_rows.append({
        "AWB Number": "BLD0088301",
        "Courier Name": "BlueDart",
        "Client Order ID": "ORD-01015",
        "Shipment Status": "Delivered",
        "Scan Timestamp": "18/10/2026 16:40",
        "Current Hub": "Bengaluru Whitefield",
        "Consignee Phone": "09845022334",
        "Consignee Name": "Ananya Menon",
        "Collectable Value": "2499.00",
        "Expected Delivery Date": "17/10/2026 18:00",
        "Delivery Attempts": "1",
        "Status Remarks": "Delivered at gate",
    })
    whatsapp_rows.append({
        "Ticket ID": "WA-1004",
        "Received At": "19/10/2026 09:45",
        "WhatsApp Number": "91 98450 22334",
        "Sender Name": "Ananya Menon",
        "Order Ref": "ORD-01015",
        "AWB": "BLD0088301",
        "Issue Type": "Pod Dispute / Not Received",
        "Message Body": "Tracking shows delivered for order ORD-01015 yesterday evening, but I have not received any package at my flat or security gate! Please share POD urgently.",
        "Ticket Status": "Open",
    })

    # 16. #01016: Older shipment event imported AFTER a newer one in the CSV
    shopify_rows.append({
        "Name": "#01016",
        "Created at": "16/10/2026 11:15",
        "Customer Name": "Deepak Nair",
        "Customer Phone": "+91 98101 10016",
        "Email": "deepak.nair@example.in",
        "Shipping City": "Hyderabad",
        "Shipping Province": "Telangana",
        "Shipping Zip": "500081",
        "Total": "₹1,199.00",
        "Payment Method": "Prepaid",
        "Financial Status": "paid",
        "Fulfillment Status": "fulfilled",
        "Cancelled at": "",
        "Lineitem SKU": "SKU-ank-08",
        "Lineitem name": "Handcrafted Silver Oxidised Jhumkas",
        "Lineitem quantity": "1",
        "Lineitem price": "1199.00",
        "Tracking Number": "XPB0010161",
        "Tracking Company": "XpressBees",
        "Promised Delivery Date": "20/10/2026 18:00",
    })
    # Notice: Newer event ("Out for Delivery" on 19 Oct) is placed BEFORE older event ("Picked Up" on 17 Oct)!
    courier_rows.extend([
        {
            "AWB Number": "XPB0010161",
            "Courier Name": "XpressBees",
            "Client Order ID": "01016",
            "Shipment Status": "Out for Delivery",
            "Scan Timestamp": "19/10/2026 09:30",
            "Current Hub": "Hyderabad Madhapur Hub",
            "Consignee Phone": "9810110016",
            "Consignee Name": "Deepak Nair",
            "Collectable Value": "1199.00",
            "Expected Delivery Date": "20/10/2026 18:00",
            "Delivery Attempts": "0",
            "Status Remarks": "Out for delivery with agent",
        },
        {
            "AWB Number": "XPB0010161",
            "Courier Name": "XpressBees",
            "Client Order ID": "01016",
            "Shipment Status": "Picked Up",
            "Scan Timestamp": "17/10/2026 14:00",
            "Current Hub": "Origin Warehouse",
            "Consignee Phone": "9810110016",
            "Consignee Name": "Deepak Nair",
            "Collectable Value": "1199.00",
            "Expected Delivery Date": "20/10/2026 18:00",
            "Delivery Attempts": "0",
            "Status Remarks": "Late-synced origin pickup scan",
        },
    ])

    # 17 & 18. #01017 & #01018: Contradictory strong identifiers (Courier row has Order #01017 but Phone of #01018)
    shopify_rows.extend([
        {
            "Name": "#01017",
            "Created at": "15/10/2026 13:00",
            "Customer Name": "Ishita Banerjee",
            "Customer Phone": "+91 98101 10017",
            "Email": "ishita.b@example.in",
            "Shipping City": "Ahmedabad",
            "Shipping Province": "Gujarat",
            "Shipping Zip": "380015",
            "Total": "₹1,499.00",
            "Payment Method": "COD",
            "Financial Status": "pending",
            "Fulfillment Status": "fulfilled",
            "Cancelled at": "",
            "Lineitem SKU": "SKU-KUR-01",
            "Lineitem name": "Indigo Handblock Cotton Kurta",
            "Lineitem quantity": "1",
            "Lineitem price": "1499.00",
            "Tracking Number": "",
            "Tracking Company": "Delhivery",
            "Promised Delivery Date": "20/10/2026 18:00",
        },
        {
            "Name": "#01018",
            "Created at": "15/10/2026 14:00",
            "Customer Name": "Gaurav Patel",
            "Customer Phone": "+91 98101 10018",
            "Email": "gaurav.p@example.in",
            "Shipping City": "Ahmedabad",
            "Shipping Province": "Gujarat",
            "Shipping Zip": "380015",
            "Total": "₹1,499.00",
            "Payment Method": "COD",
            "Financial Status": "pending",
            "Fulfillment Status": "fulfilled",
            "Cancelled at": "",
            "Lineitem SKU": "SKU-KUR-01",
            "Lineitem name": "Indigo Handblock Cotton Kurta",
            "Lineitem quantity": "1",
            "Lineitem price": "1499.00",
            "Tracking Number": "",
            "Tracking Company": "Delhivery",
            "Promised Delivery Date": "20/10/2026 18:00",
        },
    ])
    # Contradictory courier row: Client Order ID says #01017, but Consignee Phone belongs to #01018!
    courier_rows.append({
        "AWB Number": "DLV0099117",
        "Courier Name": "Delhivery",
        "Client Order ID": "#01017",
        "Shipment Status": "Failed Attempt - Consignee Unavailable",
        "Scan Timestamp": "19/10/2026 11:00",
        "Current Hub": "Ahmedabad Sanand Hub",
        "Consignee Phone": "+91 98101 10018",
        "Consignee Name": "Gaurav Patel",
        "Collectable Value": "1499.00",
        "Expected Delivery Date": "20/10/2026 18:00",
        "Delivery Attempts": "1",
        "Status Remarks": "Order ref #01017 entered on label, but consignee phone/name is #01018",
    })

    # 19. #01019: Complaint mentioning an order number in text, but from an unverified/different sender -> Needs Review
    shopify_rows.append({
        "Name": "#01019",
        "Created at": "14/10/2026 16:20",
        "Customer Name": "Divya Shetty",
        "Customer Phone": "+91 98101 10019",
        "Email": "divya.s@example.in",
        "Shipping City": "Bengaluru",
        "Shipping Province": "Karnataka",
        "Shipping Zip": "560034",
        "Total": "₹1,299.00",
        "Payment Method": "Prepaid",
        "Financial Status": "paid",
        "Fulfillment Status": "fulfilled",
        "Cancelled at": "",
        "Lineitem SKU": "SKU-BRS-03",
        "Lineitem name": "Filter Kaapi Brass Dabara Set",
        "Lineitem quantity": "1",
        "Lineitem price": "1299.00",
        "Tracking Number": "BLD0010191",
        "Tracking Company": "BlueDart",
        "Promised Delivery Date": "19/10/2026 18:00",
    })
    courier_rows.append({
        "AWB Number": "BLD0010191",
        "Courier Name": "BlueDart",
        "Client Order ID": "01019",
        "Shipment Status": "In Transit",
        "Scan Timestamp": "18/10/2026 10:00",
        "Current Hub": "Bengaluru Peenya Hub",
        "Consignee Phone": "9810110019",
        "Consignee Name": "Divya Shetty",
        "Collectable Value": "1299.00",
        "Expected Delivery Date": "19/10/2026 18:00",
        "Delivery Attempts": "0",
        "Status Remarks": "In transit",
    })
    whatsapp_rows.append({
        "Ticket ID": "WA-1005",
        "Received At": "19/10/2026 15:00",
        "WhatsApp Number": "+91 97999 00112",
        "Sender Name": "Manish Shetty",
        "Order Ref": "",
        "AWB": "",
        "Issue Type": "Delivery Follow-up",
        "Message Body": "Checking on order #01019 for my sister, can you share when BlueDart will deliver today?",
        "Ticket Status": "Open",
    })

    # 20. #01020: Contradictory shipment scan history (Delivered scan followed by In Transit scan)
    shopify_rows.append({
        "Name": "#01020",
        "Created at": "12/10/2026 15:00",
        "Customer Name": "Tanvi Kapoor",
        "Customer Phone": "+91 98101 10020",
        "Email": "tanvi.k@example.in",
        "Shipping City": "New Delhi",
        "Shipping Province": "Delhi",
        "Shipping Zip": "110016",
        "Total": "₹2,499.00",
        "Payment Method": "Prepaid",
        "Financial Status": "paid",
        "Fulfillment Status": "fulfilled",
        "Cancelled at": "",
        "Lineitem SKU": "SKU-LIN-04",
        "Lineitem name": "Mulmul Dohar Blanket - Jaipur Floral",
        "Lineitem quantity": "1",
        "Lineitem price": "2499.00",
        "Tracking Number": "ECM0010201",
        "Tracking Company": "Ecom Express",
        "Promised Delivery Date": "17/10/2026 18:00",
    })
    courier_rows.extend([
        {
            "AWB Number": "ECM0010201",
            "Courier Name": "Ecom Express",
            "Client Order ID": "01020",
            "Shipment Status": "Delivered",
            "Scan Timestamp": "16/10/2026 14:00",
            "Current Hub": "New Delhi Saket Hub",
            "Consignee Phone": "9810110020",
            "Consignee Name": "Tanvi Kapoor",
            "Collectable Value": "2499.00",
            "Expected Delivery Date": "17/10/2026 18:00",
            "Delivery Attempts": "1",
            "Status Remarks": "Marked delivered in hub batch update",
        },
        {
            "AWB Number": "ECM0010201",
            "Courier Name": "Ecom Express",
            "Client Order ID": "01020",
            "Shipment Status": "In Transit - Hub",
            "Scan Timestamp": "18/10/2026 11:30",
            "Current Hub": "Okhla Phase II Hub",
            "Consignee Phone": "9810110020",
            "Consignee Name": "Tanvi Kapoor",
            "Collectable Value": "2499.00",
            "Expected Delivery Date": "17/10/2026 18:00",
            "Delivery Attempts": "1",
            "Status Remarks": "Parcel re-scanned at Okhla sorting floor after delivery mark",
        },
    ])

    # 21. #01021: Unknown courier status code
    shopify_rows.append({
        "Name": "#01021",
        "Created at": "16/10/2026 12:30",
        "Customer Name": "Harsh Chauhan",
        "Customer Phone": "+91 98101 10021",
        "Email": "harsh.c@example.in",
        "Shipping City": "Indore",
        "Shipping Province": "Madhya Pradesh",
        "Shipping Zip": "452010",
        "Total": "₹799.00",
        "Payment Method": "Prepaid",
        "Financial Status": "paid",
        "Fulfillment Status": "fulfilled",
        "Cancelled at": "",
        "Lineitem SKU": "SKU-BAG-06",
        "Lineitem name": "Kalamkari Canvas Tote Bag",
        "Lineitem quantity": "1",
        "Lineitem price": "799.00",
        "Tracking Number": "DTC0010211",
        "Tracking Company": "DTDC",
        "Promised Delivery Date": "21/10/2026 18:00",
    })
    courier_rows.append({
        "AWB Number": "DTC0010211",
        "Courier Name": "DTDC",
        "Client Order ID": "01021",
        "Shipment Status": "STAT_CODE_99_OCTROI_VERIFICATION",
        "Scan Timestamp": "18/10/2026 18:00",
        "Current Hub": "Dewas Naka Hub",
        "Consignee Phone": "9810110021",
        "Consignee Name": "Harsh Chauhan",
        "Collectable Value": "799.00",
        "Expected Delivery Date": "21/10/2026 18:00",
        "Delivery Attempts": "0",
        "Status Remarks": "Custom vendor code not in standard dictionary",
    })

    # 22. #01022: International NRI customer phone (+971 UAE number) ordering for Bengaluru delivery
    shopify_rows.append({
        "Name": "#01022",
        "Created at": "17/10/2026 10:00",
        "Customer Name": "Nikhil Pillai",
        "Customer Phone": "+971 50 443 9981",
        "Email": "nikhil.pillai@example.in",
        "Shipping City": "Bengaluru",
        "Shipping Province": "Karnataka",
        "Shipping Zip": "560034",
        "Total": "₹2,499.00",
        "Payment Method": "Prepaid",
        "Financial Status": "paid",
        "Fulfillment Status": "fulfilled",
        "Cancelled at": "",
        "Lineitem SKU": "SKU-LIN-04",
        "Lineitem name": "Mulmul Dohar Blanket - Jaipur Floral",
        "Lineitem quantity": "1",
        "Lineitem price": "2499.00",
        "Tracking Number": "BLD0010221",
        "Tracking Company": "BlueDart",
        "Promised Delivery Date": "21/10/2026 18:00",
    })
    courier_rows.append({
        "AWB Number": "BLD0010221",
        "Courier Name": "BlueDart",
        "Client Order ID": "01022",
        "Shipment Status": "In Transit",
        "Scan Timestamp": "19/10/2026 08:30",
        "Current Hub": "Bengaluru Peenya Hub",
        "Consignee Phone": "+971504439981",
        "Consignee Name": "Nikhil Pillai",
        "Collectable Value": "2499.00",
        "Expected Delivery Date": "21/10/2026 18:00",
        "Delivery Attempts": "0",
        "Status Remarks": "Arrived at destination city hub",
    })

    # =========================================================================
    # PART 2: Bulk Reproducible Orders (#01023 to #01240 -> 240 total unique orders)
    # =========================================================================
    for order_idx in range(1023, 1241):
        order_num_str = f"#0{order_idx}"
        fn = rng.choice(FIRST_NAMES).capitalize()
        ln = rng.choice(LAST_NAMES)
        cust_name = f"{fn} {ln}"
        # Avoid colliding with "Rahul Kulkarni"
        if cust_name == "Rahul Kulkarni":
            cust_name = "Rahul Agarwal"

        phone_base = f"9876{order_idx:06d}"[-10:]
        # Vary phone formatting across rows
        fmt_choice = order_idx % 4
        if fmt_choice == 0:
            phone_shopify = f"+91 {phone_base[:5]}-{phone_base[5:]}"
            phone_courier = f"0{phone_base}"
        elif fmt_choice == 1:
            phone_shopify = f"91{phone_base}"
            phone_courier = f"+91 {phone_base}"
        elif fmt_choice == 2:
            phone_shopify = phone_base
            phone_courier = f"+91-{phone_base[:5]}-{phone_base[5:]}"
        else:
            phone_shopify = f"+91 (0) {phone_base[:5]} {phone_base[5:]}"
            phone_courier = phone_base

        city, state, pincode, hub = rng.choice(CITIES)
        sku, prod_title, price_str = rng.choice(PRODUCTS)
        courier_name, courier_prefix = rng.choice(COURIERS)
        is_cod = (order_idx % 3 == 0)
        payment_method = "Cash on Delivery" if is_cod else "Prepaid"
        fin_status = "pending" if is_cod else "paid"

        # Distribute order lifecycle deterministically
        bucket = order_idx % 20
        awb = f"{courier_prefix}00{order_idx}01"

        if bucket in (0, 1, 2, 3, 4, 5, 6, 7):
            # Delivered orders (placed 10-15 Oct, delivered 14-18 Oct)
            day_placed = 10 + (order_idx % 5)
            day_dlv = min(18, day_placed + 3)
            created_str = f"{day_placed:02d}/10/2026 11:{order_idx % 50:02d}"
            edd_str = f"{day_dlv + 1:02d}/10/2026 18:00"
            ff_status = "fulfilled"
            cancelled_str = ""
            courier_rows.append({
                "AWB Number": awb,
                "Courier Name": courier_name,
                "Client Order ID": f"ORD-0{order_idx}" if order_idx % 2 == 0 else "",
                "Shipment Status": "Delivered",
                "Scan Timestamp": f"{day_dlv:02d}/10/2026 15:{order_idx % 50:02d}",
                "Current Hub": hub,
                "Consignee Phone": phone_courier,
                "Consignee Name": cust_name,
                "Collectable Value": price_str,
                "Expected Delivery Date": edd_str,
                "Delivery Attempts": "1",
                "Status Remarks": "Delivered to consignee",
            })
        elif bucket in (8, 9, 10, 11):
            # Healthy in-transit or out-for-delivery (recent scan within 24h)
            day_placed = 16 + (order_idx % 3)
            created_str = f"{day_placed:02d}/10/2026 13:{order_idx % 50:02d}"
            edd_str = "21/10/2026 18:00"
            ff_status = "fulfilled"
            cancelled_str = ""
            status_str = "Out for Delivery" if bucket == 11 else "In Transit"
            courier_rows.append({
                "AWB Number": awb,
                "Courier Name": courier_name,
                "Client Order ID": f"0{order_idx}" if bucket != 10 else "",
                "Shipment Status": status_str,
                "Scan Timestamp": f"19/10/2026 {8 + (order_idx % 8):02d}:15",
                "Current Hub": hub,
                "Consignee Phone": phone_courier,
                "Consignee Name": cust_name,
                "Collectable Value": price_str,
                "Expected Delivery Date": edd_str,
                "Delivery Attempts": "0",
                "Status Remarks": "Moving on schedule",
            })
        elif bucket in (12, 13):
            # Newly placed unfulfilled orders (placed on 18-19 Oct, within 48h SLA -> No flag)
            created_str = f"19/10/2026 {9 + (order_idx % 7):02d}:20"
            edd_str = "24/10/2026 18:00"
            ff_status = "unfulfilled"
            cancelled_str = ""
            awb = ""
        elif bucket == 14:
            # Stale tracking in transit (last scan on 14 or 15 Oct -> >72h ago -> Flagged STALE_TRACKING)
            created_str = "13/10/2026 10:30"
            edd_str = "18/10/2026 18:00"
            ff_status = "fulfilled"
            cancelled_str = ""
            scan_day = 14 if (order_idx % 2 == 0) else 15
            courier_rows.append({
                "AWB Number": awb,
                "Courier Name": courier_name,
                "Client Order ID": f"0{order_idx}",
                "Shipment Status": "In Transit - Hub",
                "Scan Timestamp": f"{scan_day:02d}/10/2026 11:00",
                "Current Hub": hub,
                "Consignee Phone": phone_courier,
                "Consignee Name": cust_name,
                "Collectable Value": price_str,
                "Expected Delivery Date": edd_str,
                "Delivery Attempts": "0",
                "Status Remarks": "Bagged at transit facility; no onward scan",
            })
            if order_idx % 40 == 14:
                whatsapp_rows.append({
                    "Ticket ID": f"WA-{order_idx}",
                    "Received At": "19/10/2026 13:10",
                    "WhatsApp Number": phone_shopify,
                    "Sender Name": cust_name,
                    "Order Ref": f"#0{order_idx}",
                    "AWB": awb,
                    "Issue Type": "Delayed Delivery",
                    "Message Body": f"Order #0{order_idx} is stuck at {hub} for 4 days with {courier_name}. Please expedite!",
                    "Ticket Status": "Open",
                })
        elif bucket == 15:
            # Failed delivery attempt (NDR)
            created_str = "14/10/2026 14:00"
            edd_str = "18/10/2026 18:00"
            ff_status = "fulfilled"
            cancelled_str = ""
            attempts = 2 if is_cod else 1
            courier_rows.append({
                "AWB Number": awb,
                "Courier Name": courier_name,
                "Client Order ID": f"0{order_idx}",
                "Shipment Status": "Failed Attempt - Customer Unavailable",
                "Scan Timestamp": "18/10/2026 17:20",
                "Current Hub": hub,
                "Consignee Phone": phone_courier,
                "Consignee Name": cust_name,
                "Collectable Value": price_str,
                "Expected Delivery Date": edd_str,
                "Delivery Attempts": str(attempts),
                "Status Remarks": "NDR raised: customer phone switched off / door locked",
            })
        elif bucket == 16:
            # Return to origin (RTO Initiated or RTO Delivered)
            created_str = "11/10/2026 12:00"
            edd_str = "16/10/2026 18:00"
            ff_status = "fulfilled"
            cancelled_str = ""
            rto_stat = "RTO Initiated" if (order_idx % 2 == 0) else "RTO Delivered - Returned to Warehouse"
            courier_rows.append({
                "AWB Number": awb,
                "Courier Name": courier_name,
                "Client Order ID": f"0{order_idx}",
                "Shipment Status": rto_stat,
                "Scan Timestamp": "18/10/2026 16:00",
                "Current Hub": hub,
                "Consignee Phone": phone_courier,
                "Consignee Name": cust_name,
                "Collectable Value": price_str,
                "Expected Delivery Date": edd_str,
                "Delivery Attempts": "3",
                "Status Remarks": "Returning to origin warehouse after 3 failed attempts",
            })
        elif bucket == 17:
            # Old unshipped order (placed 14 or 15 Oct, still unfulfilled -> Flagged UNSHIPPED_DELAY)
            created_str = "15/10/2026 09:45"
            edd_str = "20/10/2026 18:00"
            ff_status = "unfulfilled"
            cancelled_str = ""
            awb = ""
            if order_idx % 40 == 17:
                whatsapp_rows.append({
                    "Ticket ID": f"WA-{order_idx}",
                    "Received At": "19/10/2026 10:30",
                    "WhatsApp Number": phone_shopify,
                    "Sender Name": cust_name,
                    "Order Ref": "",
                    "AWB": "",
                    "Issue Type": "Dispatch Delay",
                    "Message Body": f"Hi, I placed my order on 15 Oct ({cust_name}) and still haven't received a tracking link.",
                    "Ticket Status": "Open",
                })
        elif bucket == 18:
            # Cancelled order (not flagged)
            created_str = "14/10/2026 11:00"
            edd_str = ""
            ff_status = "cancelled"
            cancelled_str = "14/10/2026 14:00"
            awb = ""
        else:
            # bucket == 19: In transit with open customer inquiry
            created_str = "16/10/2026 15:30"
            edd_str = "20/10/2026 18:00"
            ff_status = "fulfilled"
            cancelled_str = ""
            courier_rows.append({
                "AWB Number": awb,
                "Courier Name": courier_name,
                "Client Order ID": f"0{order_idx}",
                "Shipment Status": "In Transit",
                "Scan Timestamp": "18/10/2026 22:00",
                "Current Hub": hub,
                "Consignee Phone": phone_courier,
                "Consignee Name": cust_name,
                "Collectable Value": price_str,
                "Expected Delivery Date": edd_str,
                "Delivery Attempts": "0",
                "Status Remarks": "In transit to destination hub",
            })
            if order_idx % 40 == 19:
                whatsapp_rows.append({
                    "Ticket ID": f"WA-{order_idx}",
                    "Received At": "19/10/2026 16:10",
                    "WhatsApp Number": phone_shopify,
                    "Sender Name": cust_name,
                    "Order Ref": f"0{order_idx}",
                    "AWB": awb,
                    "Issue Type": "Delivery Inquiry",
                    "Message Body": f"Hello, my order #0{order_idx} is still in transit. I need it before Diwali, please check.",
                    "Ticket Status": "Open",
                })

        shopify_rows.append({
            "Name": order_num_str,
            "Created at": created_str,
            "Customer Name": cust_name,
            "Customer Phone": phone_shopify,
            "Email": f"{fn.lower()}.{ln.lower()}.{order_idx}@example.in",
            "Shipping City": city,
            "Shipping Province": state,
            "Shipping Zip": pincode,
            "Total": f"₹{price_str}",
            "Payment Method": payment_method,
            "Financial Status": fin_status,
            "Fulfillment Status": ff_status,
            "Cancelled at": cancelled_str,
            "Lineitem SKU": sku,
            "Lineitem name": prod_title,
            "Lineitem quantity": "1",
            "Lineitem price": price_str,
            "Tracking Number": awb if (order_idx % 5 != 0) else "",
            "Tracking Company": courier_name if awb else "",
            "Promised Delivery Date": edd_str,
        })

    # =========================================================================
    # PART 3: Deliberate Unmatched, Ambiguous, and Malformed Rows
    # =========================================================================

    # Unmatched courier row 1: Only has an order amount (₹1,299.00) and generic hub, no Order ID, no Phone, no Name
    courier_rows.append({
        "AWB Number": "DLV9990001",
        "Courier Name": "Delhivery",
        "Client Order ID": "",
        "Shipment Status": "In Transit",
        "Scan Timestamp": "18/10/2026 19:00",
        "Current Hub": "Bhiwandi Mega Hub",
        "Consignee Phone": "",
        "Consignee Name": "",
        "Collectable Value": "1299.00",
        "Expected Delivery Date": "21/10/2026 18:00",
        "Delivery Attempts": "0",
        "Status Remarks": "Manual offline waybill; only COD amount 1299 recorded",
    })

    # Unmatched courier row 2: Marketplace order (Amazon/Myntra) accidentally included in D2C courier sheet
    courier_rows.append({
        "AWB Number": "BLD9990002",
        "Courier Name": "BlueDart",
        "Client Order ID": "AMZ-404-998877",
        "Shipment Status": "Out for Delivery",
        "Scan Timestamp": "19/10/2026 09:00",
        "Current Hub": "Chennai Hub",
        "Consignee Phone": "+91 91111 00999",
        "Consignee Name": "Suresh Venkat",
        "Collectable Value": "1599.00",
        "Expected Delivery Date": "19/10/2026 18:00",
        "Delivery Attempts": "0",
        "Status Remarks": "External marketplace shipment not in Shopify export",
    })

    # Unmatched WhatsApp complaint: Customer messaging from a work number not in any order
    whatsapp_rows.append({
        "Ticket ID": "WA-1098",
        "Received At": "19/10/2026 15:40",
        "WhatsApp Number": "+91 90000 88776",
        "Sender Name": "Unknown WhatsApp User",
        "Order Ref": "",
        "AWB": "",
        "Issue Type": "Unmatched Inquiry",
        "Message Body": "Hi I paid Rs 1299 via UPI 3 days ago from my husband's phone, when will my brass dabara set arrive?",
        "Ticket Status": "Open",
    })

    # Ambiguous WhatsApp complaint: Compatible name + city ("Meera Iyer", Chennai) but sent from an alternate phone without Order ID
    whatsapp_rows.append({
        "Ticket ID": "WA-1099",
        "Received At": "19/10/2026 16:25",
        "WhatsApp Number": "+91 94440 12345",
        "Sender Name": "Meera Iyer",
        "Order Ref": "",
        "AWB": "",
        "Issue Type": "Dispatch Delay",
        "Message Body": "Hello, I ordered the Mulmul Dohar Blanket for ₹2499 to Chennai on 14th Oct. Still not shipped! Messaging from my alternate number.",
        "Ticket Status": "Open",
    })

    # Deliberate Malformed Rows (one per CSV so validation & downloadable error reports are immediately demonstrable!)
    shopify_rows.append({
        "Name": "",  # Missing required Order ID
        "Created at": "19/10/2026 12:00",
        "Customer Name": "Malformed Export Row",
        "Customer Phone": "123",
        "Email": "bad@example.in",
        "Shipping City": "Mumbai",
        "Shipping Province": "Maharashtra",
        "Shipping Zip": "400001",
        "Total": "INVALID_AMOUNT",
        "Payment Method": "Prepaid",
        "Financial Status": "paid",
        "Fulfillment Status": "unfulfilled",
        "Cancelled at": "",
        "Lineitem SKU": "SKU-ERR",
        "Lineitem name": "Corrupted Spreadsheet Row",
        "Lineitem quantity": "1",
        "Lineitem price": "0",
        "Tracking Number": "",
        "Tracking Company": "",
        "Promised Delivery Date": "",
    })

    courier_rows.append({
        "AWB Number": "",  # Missing required AWB
        "Courier Name": "Delhivery",
        "Client Order ID": "#01004",
        "Shipment Status": "In Transit",
        "Scan Timestamp": "NOT_A_DATE",
        "Current Hub": "Mumbai",
        "Consignee Phone": "",
        "Consignee Name": "Corrupted Scan Row",
        "Collectable Value": "",
        "Expected Delivery Date": "",
        "Delivery Attempts": "0",
        "Status Remarks": "Row exported with blank AWB and invalid timestamp",
    })

    whatsapp_rows.append({
        "Ticket ID": "WA-ERR-99",
        "Received At": "19/10/2026 17:00",
        "WhatsApp Number": "",
        "Sender Name": "",
        "Order Ref": "",
        "AWB": "",
        "Issue Type": "Blank Export",
        "Message Body": "",  # Missing required message text & identifiers
        "Ticket Status": "Open",
    })

    # =========================================================================
    # PART 4: Ambiguous-Date Sample CSV (for testing ambiguous date detection)
    # =========================================================================
    ambiguous_shopify_rows = [
        {
            "Name": "#09001",
            "Created at": "04/05/2026 10:30",  # Could be 4 May or 5 April!
            "Customer Name": "Riya Kapoor",
            "Customer Phone": "+91 98111 22334",
            "Email": "riya@example.in",
            "Shipping City": "New Delhi",
            "Shipping Province": "Delhi",
            "Shipping Zip": "110016",
            "Total": "₹1,499.00",
            "Payment Method": "Prepaid",
            "Financial Status": "paid",
            "Fulfillment Status": "unfulfilled",
            "Cancelled at": "",
            "Lineitem SKU": "SKU-KUR-01",
            "Lineitem name": "Indigo Handblock Cotton Kurta",
            "Lineitem quantity": "1",
            "Lineitem price": "1499.00",
            "Tracking Number": "",
            "Tracking Company": "",
            "Promised Delivery Date": "08/05/2026 18:00",
        },
        {
            "Name": "#09002",
            "Created at": "06/07/2026 14:15",  # Could be 6 July or 7 June!
            "Customer Name": "Vihaan Joshi",
            "Customer Phone": "+91 98111 22335",
            "Email": "vihaan@example.in",
            "Shipping City": "Mumbai",
            "Shipping Province": "Maharashtra",
            "Shipping Zip": "400050",
            "Total": "₹899.00",
            "Payment Method": "COD",
            "Financial Status": "pending",
            "Fulfillment Status": "unfulfilled",
            "Cancelled at": "",
            "Lineitem SKU": "SKU-SRM-02",
            "Lineitem name": "Kumkumadi Glow Facial Serum 30ml",
            "Lineitem quantity": "1",
            "Lineitem price": "899.00",
            "Tracking Number": "",
            "Tracking Company": "",
            "Promised Delivery Date": "10/07/2026 18:00",
        },
    ]

    return {
        "shopify_orders_demo.csv": _dict_rows_to_csv_bytes(shopify_headers, shopify_rows),
        "courier_tracking_demo.csv": _dict_rows_to_csv_bytes(courier_headers, courier_rows),
        "whatsapp_complaints_demo.csv": _dict_rows_to_csv_bytes(whatsapp_headers, whatsapp_rows),
        "shopify_orders_ambiguous_dates.csv": _dict_rows_to_csv_bytes(shopify_headers, ambiguous_shopify_rows),
    }


def ensure_sample_files_on_disk() -> Path:
    """Write the downloadable sample CSV files to `samples/` in the project root."""
    samples_dir = BASE_DIR / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)
    bundles = generate_demo_csv_bundles()
    for fname, raw_bytes in bundles.items():
        target = samples_dir / fname
        target.write_bytes(raw_bytes)
    return samples_dir


def load_demo_dataset(db: Session, workspace_id: str) -> dict[str, Any]:
    """Reset the workspace and load all three synthetic demo CSV datasets with fixed reference time."""
    reset_workspace_data(db, workspace_id)

    settings = get_or_create_workspace_settings(db, workspace_id)
    settings.reference_time_mode = "demo_fixed"
    settings.demo_reference_iso = DEFAULT_DEMO_REFERENCE_ISO
    settings.unshipped_threshold_hours = 48
    settings.stale_tracking_threshold_hours = 72
    settings.is_demo_dataset = True
    db.flush()

    bundles = generate_demo_csv_bundles()

    s_res = ingest_csv_bytes(
        db,
        workspace_id=workspace_id,
        raw_bytes=bundles["shopify_orders_demo.csv"],
        filename="shopify_orders_demo.csv",
        source_type="shopify_orders",
        date_format_preference="DD/MM/YYYY",
        is_demo=True,
    )
    c_res = ingest_csv_bytes(
        db,
        workspace_id=workspace_id,
        raw_bytes=bundles["courier_tracking_demo.csv"],
        filename="courier_tracking_demo.csv",
        source_type="courier_tracking",
        date_format_preference="DD/MM/YYYY",
        is_demo=True,
    )
    w_res = ingest_csv_bytes(
        db,
        workspace_id=workspace_id,
        raw_bytes=bundles["whatsapp_complaints_demo.csv"],
        filename="whatsapp_complaints_demo.csv",
        source_type="whatsapp_complaints",
        date_format_preference="DD/MM/YYYY",
        is_demo=True,
    )

    settings.is_demo_dataset = True
    db.commit()

    return {
        "workspace_id": workspace_id,
        "is_demo_dataset": True,
        "reference_time_ist": DEFAULT_DEMO_REFERENCE_ISO,
        "batches": {
            "shopify_orders": s_res,
            "courier_tracking": c_res,
            "whatsapp_complaints": w_res,
        },
        "reconciliation": w_res["reconciliation"],
    }
