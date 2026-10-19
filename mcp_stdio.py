"""Standalone Model Context Protocol (MCP) Stdio Server for Order Ops & Founder Command Center.

Usage in Claude Desktop / Cursor / Antigravity MCP settings:
{
  "mcpServers": {
    "order-ops-console": {
      "command": "python",
      "args": ["D:/Order Ops Console/mcp_stdio.py"]
    }
  }
}
"""

from __future__ import annotations

import json
import sys

from src.config import get_config
from src.database import SessionLocal, create_all_tables
from src.demo_data import load_demo_dataset
from src.mcp_server import handle_mcp_jsonrpc
from src.queries import get_overview_summary


def ensure_demo_seeded() -> str:
    create_all_tables()
    ws_id = get_config().default_workspace_id
    with SessionLocal() as session:
        summary = get_overview_summary(session, ws_id)
        if summary["cards"]["total_orders"] == 0:
            load_demo_dataset(session, ws_id)
    return ws_id


def main() -> None:
    ws_id = ensure_demo_seeded()
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {exc}"},
            }
            sys.stdout.write(json.dumps(err_resp) + "\n")
            sys.stdout.flush()
            continue

        with SessionLocal() as session:
            response = handle_mcp_jsonrpc(session, payload=payload, workspace_id=ws_id)
            if response is not None:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()


if __name__ == "__main__":
    main()
