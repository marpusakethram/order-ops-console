"""Model Context Protocol (MCP) Server Implementation for Order Ops & Founder Command Center.

Supports JSON-RPC 2.0 over both:
1. HTTP endpoint (`POST /api/mcp`) for remote AI agents and the in-app MCP Studio Playground.
2. Standard I/O (`python mcp_stdio.py`) for local MCP hosts (Antigravity, Claude Desktop, Cursor).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config import get_config
from src.founder_insights import build_founder_dashboard
from src.models import Order
from src.normalization import normalize_order_id
from src.queries import execute_nl_query, get_order_details, get_overview_summary, list_orders

MCP_PROTOCOL_VERSION = "2024-11-05"
MCP_SERVER_INFO = {
    "name": "order-ops-founder-mcp",
    "version": "1.0.0",
    "description": (
        "Indian D2C Order Operations & Founder Intelligence MCP Server — reconcile Shopify orders, "
        "courier AWBs, WhatsApp complaints, COD remittances, SKU stockout runway, and unit economics."
    ),
}

ATTENTION_RULES_CATALOG = [
    {
        "rule_code": "DELIVERED_NOT_RECEIVED",
        "severity": "High",
        "description": "Courier marked shipment as Delivered, but customer reported non-receipt on WhatsApp.",
    },
    {
        "rule_code": "CONFLICTING_SHIPMENT_INFO",
        "severity": "High",
        "description": "Contradictory courier event timeline (e.g., transit scan after delivery/RTO).",
    },
    {
        "rule_code": "RTO_INITIATED",
        "severity": "High",
        "description": "Courier initiated Return-to-Origin (RTO) or returned parcel to warehouse.",
    },
    {
        "rule_code": "COMPLAINT_UNDELIVERED",
        "severity": "High",
        "description": "Open customer complaint attached to an undelivered order.",
    },
    {
        "rule_code": "FAILED_DELIVERY_ATTEMPT",
        "severity": "High / Medium",
        "description": "Courier recorded 1+ failed delivery attempts (NDR) on an undelivered shipment.",
    },
    {
        "rule_code": "STALE_TRACKING",
        "severity": "High / Medium",
        "description": "Active shipment has had no tracking scan for >= stale_tracking_threshold_hours (default 72h).",
    },
    {
        "rule_code": "UNSHIPPED_DELAY",
        "severity": "High / Medium",
        "description": "Non-cancelled order remains unfulfilled for >= unshipped_threshold_hours (default 48h).",
    },
    {
        "rule_code": "PAST_EXPECTED_DELIVERY",
        "severity": "Medium",
        "description": "Undelivered shipment is past its promised/expected delivery date.",
    },
    {
        "rule_code": "UNKNOWN_COURIER_STATUS",
        "severity": "Low",
        "description": "Raw courier status string could not be mapped to a standard lifecycle stage.",
    },
]

MCP_TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "name": "get_founder_daily_brief",
        "description": (
            "Return the Founder Morning Brief: Gross GMV, Realized Revenue, Net Contribution Margin, "
            "Overdue COD Remittances, RTO Margin Bleed, Operational Summary counts, and Top Priority Playbooks."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "sampleArguments": {},
    },
    {
        "name": "audit_cod_remittances",
        "description": (
            "Audit Cash-on-Delivery (COD) courier remittances to identify overdue payouts, "
            "short-remitted weight disputes, disputed deliveries, or at-risk NDR orders."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "status_filter": {
                    "type": "string",
                    "description": (
                        "Filter by remittance status: ALL, OVERDUE_PAYOUT, SHORT_REMITTED, "
                        "HOLD_DISPUTED_DELIVERY, AT_RISK_NDR, LOST_TO_RTO, or SETTLED."
                    ),
                    "default": "OVERDUE_PAYOUT",
                },
                "courier": {
                    "type": "string",
                    "description": "Optional courier name filter (e.g., BlueDart, Delhivery, Ecom Express).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of ledger rows to return (default 15).",
                    "default": 15,
                },
            },
            "additionalProperties": False,
        },
        "sampleArguments": {"status_filter": "OVERDUE_PAYOUT", "limit": 10},
    },
    {
        "name": "analyze_unit_economics",
        "description": (
            "Compute the brand's Gross-to-Net Contribution Margin waterfall in INR (paise accuracy): "
            "Gross GMV -> Cancelled -> RTO Loss -> COGS -> Forward & Return Courier Freight -> Net Contribution."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "sampleArguments": {},
    },
    {
        "name": "check_sku_inventory_runway",
        "description": (
            "Check SKU daily sales velocity, current warehouse inventory, days of stock remaining, "
            "RTO rate by product, and reorder purchase-order recommendations."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "only_alerts": {
                    "type": "boolean",
                    "description": "If true, return only SKUs with CRITICAL_STOCKOUT_RISK or REORDER_NOW status.",
                    "default": False,
                }
            },
            "additionalProperties": False,
        },
        "sampleArguments": {"only_alerts": False},
    },
    {
        "name": "compare_courier_performance",
        "description": (
            "Compare courier partners (Delhivery, BlueDart, Ecom Express, Xpressbees, Shadowfax, DTDC) "
            "on Delivery %, RTO %, Failed Delivery Attempt %, Average Transit Hours, and Overdue COD Cash."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "sampleArguments": {},
    },
    {
        "name": "search_flagged_orders",
        "description": (
            "Search and filter reconciled D2C orders by attention level, rule code, courier, "
            "shipment status, payment method (COD/Prepaid), or customer/AWB search term."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "attention_level": {
                    "type": "string",
                    "description": "Filter by High, Medium, Low, or any_flagged.",
                },
                "rule_code": {
                    "type": "string",
                    "description": (
                        "Specific operational rule code: DELIVERED_NOT_RECEIVED, STALE_TRACKING, "
                        "FAILED_DELIVERY_ATTEMPT, RTO_INITIATED, UNSHIPPED_DELAY, COMPLAINT_UNDELIVERED."
                    ),
                },
                "payment_method": {
                    "type": "string",
                    "description": "Filter by COD or Prepaid.",
                },
                "courier": {
                    "type": "string",
                    "description": "Filter by courier name.",
                },
                "search": {
                    "type": "string",
                    "description": "Search by order number, tracking AWB, or customer name.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max orders to return (default 10).",
                    "default": 10,
                },
            },
            "additionalProperties": False,
        },
        "sampleArguments": {"attention_level": "High", "limit": 8},
    },
    {
        "name": "inspect_order_provenance",
        "description": (
            "Get full 360-degree details and CSV row provenance for a specific order number "
            "(e.g., '#01004', '01002', or '001234'), including shipments, chronological timeline, "
            "WhatsApp complaints, attention flags, and suggested next actions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "Order number (e.g., '#01004' or '001234').",
                }
            },
            "required": ["order_id"],
            "additionalProperties": False,
        },
        "sampleArguments": {"order_id": "#01004"},
    },
    {
        "name": "run_natural_language_ops_query",
        "description": (
            "Ask a natural-language question against the validated Order Ops query engine "
            "(e.g., 'Which COD orders had a failed delivery attempt?' or 'Show shipments with no update for three days')."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Natural-language question about orders, shipments, or complaints.",
                }
            },
            "required": ["question"],
            "additionalProperties": False,
        },
        "sampleArguments": {"question": "Which COD orders had a failed delivery attempt?"},
    },
]

MCP_RESOURCES_SCHEMA: list[dict[str, Any]] = [
    {
        "uri": "orderops://founder/daily-brief",
        "name": "Founder Morning Brief & Cashflow Pulse",
        "description": "Live executive summary of GMV, Net Margin, Overdue COD Remittances, and Top 4 Founder Playbooks.",
        "mimeType": "application/json",
    },
    {
        "uri": "orderops://founder/cod-remittance-ledger",
        "name": "COD Remittance & Discrepancy Ledger",
        "description": "Courier COD payout audit trail including overdue settlements and volumetric weight disputes.",
        "mimeType": "application/json",
    },
    {
        "uri": "orderops://founder/sku-inventory-runway",
        "name": "SKU Velocity & Stockout Runway Report",
        "description": "SKU-level burn rate, warehouse units remaining, RTO rate, and reorder recommendations.",
        "mimeType": "application/json",
    },
    {
        "uri": "orderops://ops/attention-rules",
        "name": "Operational Attention Rules Catalog",
        "description": "Definitions and severity levels of all 9 deterministic D2C order reconciliation rules.",
        "mimeType": "application/json",
    },
]

MCP_PROMPTS_SCHEMA: list[dict[str, Any]] = [
    {
        "name": "morning_standup_audit",
        "description": "Generate a 5-bullet Founder & Ops Morning Standup memo covering stuck orders, COD cash leakage, and stockout risks.",
        "arguments": [],
    },
    {
        "name": "courier_rto_negotiation_brief",
        "description": "Draft a data-backed SLA escalation & rate negotiation memo for courier account managers.",
        "arguments": [
            {
                "name": "courier_name",
                "description": "Target courier partner (e.g., Delhivery, Xpressbees, BlueDart).",
                "required": False,
            }
        ],
    },
    {
        "name": "cod_leakage_investigation",
        "description": "Investigate overdue COD remittances and short-paid volumetric weight deductions.",
        "arguments": [],
    },
]


def get_mcp_catalog(project_root: Path | None = None) -> dict[str, Any]:
    """Return full MCP server metadata, tools, resources, prompts, and client config snippets."""
    root_path = str((project_root or Path(__file__).resolve().parent.parent).resolve())
    stdio_script = str(Path(root_path) / "mcp_stdio.py")

    return {
        "protocol_version": MCP_PROTOCOL_VERSION,
        "server_info": MCP_SERVER_INFO,
        "tools": MCP_TOOLS_SCHEMA,
        "resources": MCP_RESOURCES_SCHEMA,
        "prompts": MCP_PROMPTS_SCHEMA,
        "client_configs": {
            "antigravity_or_claude_stdio": {
                "mcpServers": {
                    "order-ops-console": {
                        "command": "python",
                        "args": [stdio_script],
                        "cwd": root_path,
                    }
                }
            },
            "http_jsonrpc_endpoint": "http://127.0.0.1:8000/api/mcp",
        },
    }


def call_mcp_tool_by_name(
    session: Session,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Execute one of the registered MCP tools against the live database session."""
    ws_id = workspace_id or get_config().default_workspace_id
    args = arguments or {}

    if tool_name == "get_founder_daily_brief":
        overview = get_overview_summary(session, ws_id)
        founder = build_founder_dashboard(session, workspace_id=ws_id)
        return {
            "reference_time_ist": founder["reference_time_ist"],
            "operational_cards": overview["cards"],
            "unit_economics_summary": founder["unit_economics"],
            "founder_playbooks": founder["founder_playbooks"],
        }

    if tool_name == "audit_cod_remittances":
        founder = build_founder_dashboard(session, workspace_id=ws_id)
        status_filter = str(args.get("status_filter") or "OVERDUE_PAYOUT").strip().upper()
        courier_filter = str(args.get("courier") or "").strip().lower()
        limit = max(1, min(int(args.get("limit") or 15), 100))

        rows = founder["cod_remittance_ledger"]
        if status_filter and status_filter != "ALL":
            rows = [r for r in rows if r["remittance_status"] == status_filter]
        if courier_filter:
            rows = [r for r in rows if courier_filter in (r["courier_name"] or "").lower()]

        return {
            "reference_time_ist": founder["reference_time_ist"],
            "applied_filters": {"status_filter": status_filter, "courier": courier_filter or None, "limit": limit},
            "total_matching_cod_orders": len(rows),
            "overdue_cod_total_display": founder["unit_economics"]["cod_overdue_display"],
            "shortfall_discrepancy_display": founder["unit_economics"]["cod_shortfall_discrepancy_display"],
            "rows": rows[:limit],
        }

    if tool_name == "analyze_unit_economics":
        founder = build_founder_dashboard(session, workspace_id=ws_id)
        return {
            "reference_time_ist": founder["reference_time_ist"],
            "unit_economics": founder["unit_economics"],
        }

    if tool_name == "check_sku_inventory_runway":
        founder = build_founder_dashboard(session, workspace_id=ws_id)
        only_alerts = bool(args.get("only_alerts", False))
        skus = founder["sku_inventory_runway"]
        if only_alerts:
            skus = [
                s
                for s in skus
                if s["stock_status"] in ("CRITICAL_STOCKOUT_RISK", "REORDER_NOW")
            ]
        return {
            "reference_time_ist": founder["reference_time_ist"],
            "only_alerts": only_alerts,
            "sku_count": len(skus),
            "skus": skus,
        }

    if tool_name == "compare_courier_performance":
        founder = build_founder_dashboard(session, workspace_id=ws_id)
        return {
            "reference_time_ist": founder["reference_time_ist"],
            "couriers": founder["courier_scorecard"],
        }

    if tool_name == "search_flagged_orders":
        limit = max(1, min(int(args.get("limit") or 10), 50))
        res = list_orders(
            session,
            ws_id,
            search=str(args.get("search") or ""),
            attention_level=str(args.get("attention_level") or ""),
            rule_code=str(args.get("rule_code") or ""),
            payment_method=str(args.get("payment_method") or ""),
            courier=str(args.get("courier") or ""),
            page=1,
            page_size=limit,
        )
        return {
            "total_matching_orders": res["total"],
            "returned_count": len(res["items"]),
            "orders": res["items"],
        }

    if tool_name == "inspect_order_provenance":
        raw_id = str(args.get("order_id") or "").strip()
        if not raw_id:
            raise ValueError("Argument 'order_id' is required.")
        norm_id = normalize_order_id(raw_id) or raw_id
        order_obj = session.scalar(
            select(Order).where(
                Order.workspace_id == ws_id,
                (Order.order_number_norm == norm_id) | (Order.order_number_raw == raw_id),
            )
        )
        if not order_obj and raw_id.isdigit():
            order_obj = session.scalar(
                select(Order).where(Order.workspace_id == ws_id, Order.id == int(raw_id))
            )
        if not order_obj:
            raise ValueError(f"Order '{raw_id}' (normalized: '{norm_id}') was not found in workspace '{ws_id}'.")
        return get_order_details(session, ws_id, order_obj.id)

    if tool_name == "run_natural_language_ops_query":
        question = str(args.get("question") or "").strip()
        if not question:
            raise ValueError("Argument 'question' is required.")
        return execute_nl_query(session, ws_id, question)

    raise ValueError(f"Unknown MCP tool: '{tool_name}'")


def read_mcp_resource_by_uri(
    session: Session,
    uri: str,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Read an MCP resource URI and return its structured content."""
    ws_id = workspace_id or get_config().default_workspace_id
    founder = build_founder_dashboard(session, workspace_id=ws_id)

    if uri == "orderops://founder/daily-brief":
        payload = {
            "reference_time_ist": founder["reference_time_ist"],
            "unit_economics": founder["unit_economics"],
            "founder_playbooks": founder["founder_playbooks"],
        }
    elif uri == "orderops://founder/cod-remittance-ledger":
        payload = {
            "reference_time_ist": founder["reference_time_ist"],
            "ledger_count": len(founder["cod_remittance_ledger"]),
            "items": founder["cod_remittance_ledger"][:30],
        }
    elif uri == "orderops://founder/sku-inventory-runway":
        payload = {
            "reference_time_ist": founder["reference_time_ist"],
            "skus": founder["sku_inventory_runway"],
        }
    elif uri == "orderops://ops/attention-rules":
        payload = {"rules": ATTENTION_RULES_CATALOG}
    else:
        raise ValueError(f"Unknown MCP resource URI: '{uri}'")

    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": "application/json",
                "text": json.dumps(payload, indent=2),
            }
        ]
    }


def get_mcp_prompt_by_name(
    session: Session,
    name: str,
    arguments: dict[str, Any] | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Generate a contextualized MCP prompt template populated with live workspace metrics."""
    ws_id = workspace_id or get_config().default_workspace_id
    args = arguments or {}
    founder = build_founder_dashboard(session, workspace_id=ws_id)
    ue = founder["unit_economics"]

    if name == "morning_standup_audit":
        prompt_text = (
            f"You are advising the Founder & Head of Operations of an Indian D2C brand (Reference Time: {founder['reference_time_ist']}).\n"
            f"- Active GMV: {ue['active_gmv_display']} | Net Contribution: {ue['net_contribution_display']} ({ue['net_contribution_pct']}%)\n"
            f"- Overdue COD Remittances: {ue['cod_overdue_display']} across {ue['overdue_cod_count']} delivered orders\n"
            f"- RTO Margin Bleed: {ue['rto_margin_bleed_display']}\n"
            "Use the `get_founder_daily_brief`, `audit_cod_remittances`, and `check_sku_inventory_runway` tools to produce a "
            "concise 5-point action plan prioritized by INR cash impact."
        )
    elif name == "courier_rto_negotiation_brief":
        target_courier = args.get("courier_name") or "all carriers"
        prompt_text = (
            f"Prepare a carrier SLA review and rate-card negotiation memo for {target_courier} "
            f"(Reference Time: {founder['reference_time_ist']}).\n"
            "Call `compare_courier_performance` and `audit_cod_remittances` to cite exact RTO percentages, "
            "stale shipments ≥72h, volumetric weight disputes, and overdue COD settlements."
        )
    elif name == "cod_leakage_investigation":
        prompt_text = (
            f"Audit the brand's COD cashflow leakage as of {founder['reference_time_ist']}.\n"
            f"Currently {ue['cod_overdue_display']} is overdue past T+4 SLA and {ue['cod_shortfall_discrepancy_display']} "
            "was deducted in courier weight disputes. Call `audit_cod_remittances` and list the specific AWBs and UTRs to escalate."
        )
    else:
        raise ValueError(f"Unknown MCP prompt: '{name}'")

    return {
        "description": f"Contextual prompt for {name}",
        "messages": [
            {
                "role": "user",
                "content": {"type": "text", "text": prompt_text},
            }
        ],
    }


def handle_mcp_jsonrpc(
    session: Session,
    payload: dict[str, Any],
    workspace_id: str | None = None,
) -> dict[str, Any] | None:
    """Process a standard Model Context Protocol JSON-RPC 2.0 request and return a JSON-RPC 2.0 response."""
    ws_id = workspace_id or get_config().default_workspace_id
    req_id = payload.get("id")
    method = payload.get("method", "")
    params = payload.get("params") or {}

    if method.startswith("notifications/"):
        return None

    try:
        if method == "initialize":
            result = {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                    "prompts": {"listChanged": False},
                },
                "serverInfo": MCP_SERVER_INFO,
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": t["name"],
                        "description": t["description"],
                        "inputSchema": t["inputSchema"],
                    }
                    for t in MCP_TOOLS_SCHEMA
                ]
            }
        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments") or {}
            tool_output = call_mcp_tool_by_name(
                session,
                tool_name=tool_name,
                arguments=tool_args,
                workspace_id=ws_id,
            )
            result = {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(tool_output, indent=2),
                    }
                ],
                "structuredContent": tool_output,
                "isError": False,
            }
        elif method == "resources/list":
            result = {"resources": MCP_RESOURCES_SCHEMA}
        elif method == "resources/read":
            uri = params.get("uri", "")
            result = read_mcp_resource_by_uri(session, uri=uri, workspace_id=ws_id)
        elif method == "prompts/list":
            result = {"prompts": MCP_PROMPTS_SCHEMA}
        elif method == "prompts/get":
            prompt_name = params.get("name", "")
            prompt_args = params.get("arguments") or {}
            result = get_mcp_prompt_by_name(
                session,
                name=prompt_name,
                arguments=prompt_args,
                workspace_id=ws_id,
            )
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}",
                },
            }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": result,
        }
    except Exception as exc:
        if method == "tools/call":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {exc}"}],
                    "isError": True,
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": -32602,
                "message": str(exc),
            },
        }
