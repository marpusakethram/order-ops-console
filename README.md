# Order Ops Console

An operations console built for small Indian direct-to-consumer (D2C) brands to reconcile **Shopify order exports**, **courier tracking spreadsheets**, and **exported WhatsApp customer complaint logs** into a single, explainable view.

---

## 1. What the Application Does

Operations teams at small Indian D2C brands typically track daily orders across three disconnected spreadsheets:
1. **Shopify Orders CSV** — contains order numbers (`#01004`), line items, rupee totals, payment methods (`COD` / `Prepaid`), and customer phone numbers.
2. **Courier Tracking CSV** — exported from logistics partners (Delhivery, BlueDart, XpressBees, Ecom Express, DTDC, Shadowfax) with AWB numbers, scan timestamps, hub locations, and NDR/RTO statuses.
3. **WhatsApp Customer Complaints CSV** — exported ticket/chat logs where customers ask about delayed orders, fake delivery attempts, or parcels marked delivered but not received.

Because these three sources use different order ID prefixes (`#01004` vs `ORD-01004`), inconsistent phone formats (`+91 98450-22334` vs `09845022334`), and different status labels, ops employees spend hours doing manual `VLOOKUP` checks in Excel.

**Order Ops Console** brings all three sources together so an operations employee can immediately answer:
- **Which orders are stuck?** (Awaiting shipment past SLA, stale tracking in transit, or past expected delivery date)
- **Which shipments have had no tracking updates?**
- **Which orders have failed delivery attempts (NDR)?**
- **Which orders are returning to the warehouse (RTO)?**
- **Which customers have complained about undelivered orders—or orders marked "Delivered" that they never received?**
- **Which orders need attention first?**
- **Which source records could not be matched confidently and need human review?**

Every flag and match includes a plain-English explanation and links directly back to the exact **source filename and spreadsheet row number**.

---

## 2. Current Implementation Status

| Feature Area | Status | Notes |
| :--- | :--- | :--- |
| **Demo Mode (Local SQLite)** | Implemented & Tested | Works out of the box with zero API keys or external database setup. |
| **Connected Mode (Supabase PostgreSQL)** | Implemented & Tested | Uses the exact same SQLAlchemy models and business logic; includes RLS policy script in `supabase/schema_and_rls.sql`. |
| **CSV Import, Mapping & Validation** | Implemented & Tested | Supports Shopify, Courier, and WhatsApp CSVs, column mapping overrides, ambiguous date detection, idempotency, and downloadable CSV error reports. |
| **Deterministic Reconciliation Engine** | Implemented & Tested | 5-level matching hierarchy + manual Match Review queue and audit history. |
| **Operational Attention Rules** | Implemented & Tested | 9 transparent, configurable rules with priority levels (`High`, `Medium`, `Low`, `No current flag`). |
| **Ask Your Orders (NL Query + Gemini)** | Implemented & Tested | Uses Gemini (`gemini-2.5-flash`) when `GEMINI_API_KEY` is set, with a deterministic parser fallback when no key is configured. |
| **Safe CSV Export** | Implemented & Tested | Exports filtered order views with spreadsheet formula-injection protection. |

> **Honesty Note:** This is a portfolio and operational reference implementation built with synthetic Indian D2C data. It has not been deployed at a commercial brand or used with live customer PII.

---

## 3. Architecture in Simple Language

What happens from CSV upload to final result:

```mermaid
flowchart TD
    A["1. Upload CSVs or Click 'Load Demo Data'<br/>(Shopify, Courier, WhatsApp CSV Logs)"] --> B["2. CSV Parser & Validator (src/csv_parser.py)<br/>Detects encoding, maps columns, flags ambiguous dates,<br/>preserves leading zeros & original row numbers"]
    B --> C["3. Raw & Normalized Storage (src/importer.py)<br/>Saves immutable RawSourceRecord rows + consolidates<br/>Orders, Line Items, Shipments, Events & Complaints"]
    C --> D["4. Deterministic Reconciliation (src/matching.py)<br/>Matches Courier & Complaint rows to Orders using<br/>Order ID -> AWB -> Validated Phone -> Manual Review"]
    D --> E["5. Operational Attention Rules (src/attention_rules.py)<br/>Evaluates Stale Tracking, Unshipped SLA, NDR, RTO,<br/>Delivered-Not-Received & Scan Conflicts in IST"]
    E --> F["6. Operations Web Console (FastAPI + HTML/CSS/JS)<br/>Overview · Import · Orders · Order Details · Match Review · Ask"]
```

### Why this architecture?
- **Python + FastAPI** serves both the JSON API (`/api/...`) and the static web interface (`/static/...`) from a single process.
- **Plain HTML, CSS, and Vanilla JavaScript** for the frontend means no Node.js build step is needed—making it fast to start, easy for a beginner to inspect, and resilient to bundler errors.
- **SQLAlchemy 2.0** allows the exact same Python code to run against a local file (`data/order_ops_console.db` in Demo Mode) or **Supabase PostgreSQL** (`DATABASE_URL` in Connected Mode).
- **Money is stored in integer paise** (`total_amount_paise`, where `₹1,499.50` = `149950` paise) using `Decimal` arithmetic—never binary floating-point numbers.
- **Timestamps are stored in UTC and displayed in Indian Standard Time (`Asia/Kolkata`, `UTC+05:30`)**. When an imported spreadsheet timestamp has no timezone offset, it is explicitly assumed to be in `Asia/Kolkata (IST)`.

### Code Organization
- `main.py` — Root entrypoint for starting the server (`uvicorn main:app`).
- `src/config.py` — Environment settings, Demo vs. Connected mode detection, and IST reference clock.
- `src/database.py` — SQLAlchemy engine and session management.
- `src/models.py` — All 10 relational tables (`orders`, `order_line_items`, `shipments`, `shipment_events`, `complaints`, `import_batches`, `raw_source_records`, `match_decisions`, `attention_flags`, `settings`).
- `src/normalization.py` — Order ID, AWB, Indian/international phone, paise money, IST date parsing, and CSV injection sanitization.
- `src/csv_parser.py` — Encoding fallback, automatic column mapping suggestions, and row-level validation.
- `src/matching.py` — Deterministic matching hierarchy and manual match audit trail.
- `src/attention_rules.py` — Transparent operational rules and priority flags.
- `src/nl_query.py` — Natural-language question parser (Gemini structured output + deterministic fallback).
- `src/queries.py` — Dashboard aggregations, order search/filtering, timeline assembly, and CSV export.
- `src/routes.py` — FastAPI API endpoints.
- `src/demo_data.py` — Fixed-seed (`20261019`) synthetic generator for 240 Indian D2C orders and edge cases.
- `src/static/` — `index.html`, `styles.css`, and `app.js`.
- `supabase/schema_and_rls.sql` — Supabase PostgreSQL Row Level Security (RLS) policies.
- `tests/test_order_ops.py` — Automated `pytest` suite covering business logic, edge cases, and security.

---

## 4. Quick Start (Local Demo Setup on Windows PowerShell)

No API keys, paid services, or database setup are required for Demo Mode.

### Step 1: Open PowerShell in the project folder
```powershell
cd "D:\Order Ops Console"
```

### Step 2: Install dependencies
```powershell
python -m pip install -r requirements.txt
```

### Step 3: Start the application
```powershell
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```
*(Or run the included convenience script `.\start.ps1`, which checks Python and launches Uvicorn on port 8000).*

### Step 4: Open the Console & Load Demo Data
1. Open **http://127.0.0.1:8000** in your browser.
2. Click the **"Load Demo Data"** button in the top-right header.
3. All **240 synthetic orders**, courier scans, and WhatsApp complaints will load and reconcile immediately.

> **Why does the demo show a fixed Reference Time (`19 Oct 2026, 06:00 PM IST`)?**
> Time-based rules like *"No tracking update for 3 days"* depend on the current clock. By anchoring Demo Mode to a clearly displayed reference timestamp (`19 Oct 2026, 06:00 PM IST`), the demo dataset behaves identically whether you open the project today or six months from now. You can switch between **Demo Fixed Reference** and **Live Wall Clock (IST)** anytime in **Overview → Rule Thresholds**.

---

## 5. Connected Mode Setup (Supabase PostgreSQL & Optional Gemini API)

### How Demo Mode (SQLite) Differs from Connected Mode (Supabase PostgreSQL)
| Aspect | Demo Mode (Default) | Connected Mode (Supabase) |
| :--- | :--- | :--- |
| **Database Engine** | Local SQLite file (`data/order_ops_console.db`) | Hosted Supabase PostgreSQL (`DATABASE_URL`) |
| **Setup Required** | Zero configuration | Set `DATABASE_URL` in `.env` |
| **Multi-User Isolation** | Single local `"demo-workspace"` | Per-user `workspace_id` + PostgreSQL Row Level Security (`supabase/schema_and_rls.sql`) |
| **Authentication** | Optional (`REQUIRE_AUTH=false`) | Enforced when `REQUIRE_AUTH=true` via Supabase Auth JWT (`SUPABASE_JWT_SECRET`) or `APP_ACCESS_TOKEN` |
| **Business Logic** | Identical SQLAlchemy ORM & rules | Identical SQLAlchemy ORM & rules |

### Enabling Connected Mode
1. Copy `.env.example` to `.env`:
   ```powershell
   Copy-Item .env.example .env
   ```
2. In your Supabase dashboard, go to **Project Settings → Database → Connection string (URI)** and set `DATABASE_URL` in `.env`:
   ```env
   APP_MODE=connected
   DATABASE_URL=postgresql+psycopg://postgres.[project-ref]:[password]@aws-0-ap-south-1.pooler.supabase.com:6543/postgres
   REQUIRE_AUTH=true
   SUPABASE_JWT_SECRET=your-supabase-jwt-secret
   ```
3. Install the PostgreSQL driver if not already installed:
   ```powershell
   python -m pip install "psycopg[binary]>=3.1.18"
   ```
4. Start the app once so SQLAlchemy creates all 10 tables, then paste and run `supabase/schema_and_rls.sql` in the **Supabase SQL Editor** to enable Row Level Security (RLS).

### Enabling Optional Gemini API for "Ask Your Orders"
1. Obtain a Gemini API key from Google AI Studio.
2. Add it to your `.env` file:
   ```env
   GEMINI_API_KEY=your-gemini-api-key
   GEMINI_MODEL=gemini-2.5-flash
   ```
3. **Security Design:** Gemini is used *only* to translate a user's short question into a strict Pydantic filter schema (`StructuredOrderQuery`). It never receives customer datasets or complaint text, and never generates or executes raw SQL. When `GEMINI_API_KEY` is blank, the built-in deterministic query parser handles all supported questions automatically.

---

## 6. Expected CSV Formats & Column Variations

Columns can appear in **any order**, and extra columns are safely ignored. You can also download ready-to-use sample CSVs directly from the **Import Data** tab or the `samples/` folder.

### 1. Shopify Orders CSV (`shopify_orders`)
- **Required fields:**
  - **Order ID:** `Name`, `Order ID`, `Order Number`, `Order #`
  - **Order Date:** `Created at`, `Order Date`, `Date`
  - **Order Total:** `Total`, `Order Value`, `Total Amount`, `Amount`
- **Optional fields:** `Customer Name`, `Customer Phone` (`Phone` / `Mobile`), `Email`, `Shipping City` (`City`), `Shipping Province` (`State`), `Shipping Zip` (`Pincode`), `Payment Method`, `Financial Status`, `Fulfillment Status`, `Cancelled at`, `Lineitem SKU`, `Lineitem name`, `Lineitem quantity`, `Lineitem price`, `Tracking Number`, `Tracking Company`.
- **Line-item consolidation:** If an order has multiple rows for different SKUs (like `#01008` or `#01010`), they are consolidated into a single Order record with multiple line items so the order and its rupee total are never double-counted.

### 2. Courier Tracking CSV (`courier_tracking`)
- **Required fields:**
  - **Tracking Number:** `AWB Number`, `Tracking Number`, `AWB`, `Waybill`
  - **Shipment Status:** `Shipment Status`, `Status`, `Current Status`, `Scan Status`
  - **Scan Timestamp:** `Scan Timestamp`, `Event Time`, `Last Update`, `Updated At`, `Date`
- **Optional fields:** `Courier Name`, `Client Order ID` (`Order ID` / `Reference No`), `Current Hub` (`Location`), `Consignee Phone`, `Consignee Name`, `Collectable Value` (`COD Amount`), `Expected Delivery Date`, `Delivery Attempts`, `Status Remarks`.

### 3. WhatsApp Customer Complaints CSV (`whatsapp_complaints`)
> **Important:** WhatsApp complaints are imported from CSV log files—not a live connection to a WhatsApp account.
- **Required fields:**
  - **Complaint Timestamp:** `Received At`, `Timestamp`, `Date`, `Message Time`
  - **Message Text:** `Message Body`, `Message`, `Complaint Text`, `Customer Message`
  - *(Plus at least one identifier column or mention: `WhatsApp Number` / `Phone`, `Order Ref`, `AWB`, or `Sender Name`)*
- **Optional fields:** `Ticket ID`, `Issue Type`, `Ticket Status`.

### Ambiguous Date Handling & Deduplication Rules
- **Ambiguous dates:** If a file contains numeric dates like `04/05/2026` where both numbers are `≤ 12`, `Auto-detect` mode pauses and asks you to confirm whether the file uses `DD/MM/YYYY` (Indian standard) or `MM/DD/YYYY` rather than guessing silently.
- **Idempotency (Deduplication):** Every row is fingerprinted deterministically (`row_fingerprint`). Uploading the same CSV file twice skips duplicate rows, increments the `Duplicate Rows` counter, and never creates duplicate orders, shipments, scan events, or complaints.
- **Deleting an Import:** In the **Overview → Recent Import Batches** table, click **Delete** next to any batch to remove that batch's records and automatically re-reconcile the workspace.

---

## 7. Reconciliation & Matching Rules

Records are reconciled using a conservative, deterministic hierarchy (never AI-guessed):

1. **Exact Normalized Order Identifier (`Exact match`, Heuristic Score: 100)**
   - Matches `#01004`, `ORD-01004`, and `01004` while preserving leading zeros (`01004` ≠ `1004`).
2. **Exact Tracking Number / AWB (`Exact match`, Heuristic Score: 95)**
   - Matches normalized AWB numbers (`00BLD10001`) linked to an order.
3. **Order Number in Complaint Text + Supporting Evidence (`Strong match`, Heuristic Score: 90)**
   - If a WhatsApp message mentions `#01004` *and* the sender's phone or name matches the order, it links as a `Strong match`. If the sender's phone/name is unverified (e.g., `#01019`), it routes to **Needs review**.
4. **Exact Normalized Phone + Supporting Evidence (`Strong match`, Heuristic Score: 85)**
   - Normalizes Indian numbers (`+91 98220-33445`, `09822033445`, `919822033445` → `+919822033445`) while keeping international numbers (`+971...`) intact.
   - Auto-matches **only** if that phone belongs to a single order *and* is corroborated by compatible customer name, amount, city, or date window.
5. **Hard Safety Rules (Routed to `Needs review` or `Unmatched`):**
   - **Shared household phone (`#01011` & `#01012`):** Never auto-matched when multiple orders share a phone number without an Order ID/AWB.
   - **Customer name alone (`#01013` & `#01014` — `"Rahul Kulkarni"`):** Never auto-matched.
   - **Order amount alone (`AWB DLV9990001` — `₹1,299`):** Never auto-matched.
   - **Conflicting strong identifiers (`#01017` vs `#01018`):** If the Order ID points to one order but the phone or AWB belongs to another, automatic matching is blocked and routed to **Match Review**.
   - **Late-arriving older courier scans (`#01016`):** Added in chronological order to the shipment timeline without overwriting a newer shipment status.

---

## 8. Operational Attention Rules

Attention levels (`High`, `Medium`, `Low`, `No current flag`) represent **operational priority**, not a statistical probability of delivery failure.

| Rule Code | Priority | Trigger Condition & Default Threshold | Exclusions & Edge Cases |
| :--- | :--- | :--- | :--- |
| `DELIVERED_NOT_RECEIVED` | **High** | Courier status is `Delivered`, but an open customer complaint claims the package was not received (`#01015`). | Resolved complaints are excluded. |
| `CONFLICTING_SHIPMENT_INFO` | **High** | Contradictory scan sequence (e.g., `In Transit` scan *after* a `Delivered` scan on `#01020`, or active shipment on a cancelled order). |Chronological scan order is checked across all events. |
| `RTO_INITIATED` | **High / Med** | Shipment status is `RTO Initiated` or `RTO Delivered` (`#01006`). | — |
| `COMPLAINT_UNDELIVERED` | **High** | Open customer complaint attached to an order that is not yet delivered (`#01004`, `#01005`). | — |
| `FAILED_DELIVERY_ATTEMPT` | **High / Med** | Courier recorded 1+ failed delivery attempts / NDR (`High` if COD or ≥ 2 attempts; `Medium` if 1 attempt). | Not triggered once delivered or returned. |
| `STALE_TRACKING` | **High / Med** | Active moving shipment (`awaiting_pickup`, `in_transit`, `out_for_delivery`) has no scan for **≥ 72 hours (3 days)** (`#01004`). `High` if ≥ 120h. | **Delivered, RTO, and Cancelled shipments are never flagged as stale.** |
| `UNSHIPPED_DELAY` | **High / Med** | Non-cancelled order remains unfulfilled without dispatch for **≥ 48 hours (2 days)** (`#01003`). | **Cancelled orders (`#01007`) are never flagged.** |
| `PAST_EXPECTED_DELIVERY` | **Medium** | Undelivered, non-cancelled shipment is past its expected/promised delivery date. | — |
| `UNKNOWN_COURIER_STATUS` | **Low** | Courier status code is not in the standard dictionary (`#01021` — `STAT_CODE_99_OCTROI_VERIFICATION`). | Flagged for manual inspection rather than ignored. |

---

## 9. Running Automated Tests

Run the full test suite from PowerShell:

```powershell
python -m pytest -v
```

The test suite (`tests/test_order_ops.py`) verifies:
- Identifier, leading-zero, Indian/international phone, and Decimal paise normalization.
- Ambiguous date detection (`04/05/2026`) and IST timezone handling.
- Column mapping and required-field validation.
- Shopify multi-line-item consolidation and duplicate import idempotency.
- Multiple shipments per order and chronological ordering of out-of-order courier events.
- Ambiguous matches (shared phone, shared name, amount-only) and conflicting strong identifiers.
- Manual match confirmation, override, and audit logging without mutating raw source rows.
- Attention rule thresholds, cancelled order exclusions, and "Delivered but not received" disputes.
- Natural-language query disambiguation (`"Which orders are stuck?"`), supported filters, unsupported query guardrails, and Gemini fallback behavior.
- Tenant/workspace isolation, JWT/token access control, and CSV formula injection protection.

---

## 10. 3-Minute Recruiter Demo Script

1. **Load & Orient (Overview Page):**
   - Click **"Load Demo Data"** in the header. Point out the yellow **Synthetic Demo Data** banner and the fixed reference clock (`19 Oct 2026, 06:00 PM IST`).
   - Show the 9 operational summary cards (240 total orders, stale tracking, failed delivery attempts, RTO, open complaints, and 6 unmatched/ambiguous source records).
2. **Explain a Flagged Order with Source Evidence (`#01004` & `#01015`):**
   - Click **"Demo Walkthrough Guide"** → **Step 1 (`#01004`)** or click the **Orders with Stale Tracking** card.
   - Open **Order `#01004`**: show the two High-priority flags (*Stale Tracking Update: 5 days vs 3-day threshold* and *Customer Complaint on Undelivered Order*), the exact CSV filenames and row numbers, the chronological timeline, and the suggested next actions.
   - Click **Step 2 (`#01015`)** to show a **Delivered Status Disputed by Customer** flag where BlueDart marked the parcel delivered yesterday, but the customer's WhatsApp ticket states it never arrived at her gate.
3. **Resolve an Ambiguous Record (Match Review Page):**
   - Click **4. Match Review** in the navigation bar.
   - Point out **Ticket `WA-1003`** (where household phone `+91 •••••• 1223` is shared by two Mumbai orders: `#01011` and `#01012`) and **AWB `DLV0099117`** (where the label says Order `#01017` but the phone belongs to `#01018`).
   - Click **"✓ Confirm Match to #01012"** on `WA-1003` and scroll down to show the new entry in the **Manual Match Audit History** table while the raw imported fields remain untouched.
4. **Ask Natural-Language Questions (Ask Your Orders Page):**
   - Click **5. Ask Your Orders** and click the chip **"Which orders are stuck?"**
   - Show how the console refuses to guess silently and offers 3 clear definitions of "stuck" (*Awaiting shipment > 2 days*, *In transit with no update > 3 days*, *Past expected delivery date*). Click one of the choices to run the query.
   - Next, click **"Which COD orders had a failed delivery attempt?"**, review the plain-English explanation and filters used, click **"Open Results in Orders Page"**, and click **"⬇ Export Filtered CSV"**.

---

## 11. Deployment (Vercel & Supabase)

This repository includes a zero-build FastAPI configuration (`main.py` and `vercel.json`) compatible with Vercel's Python runtime.

### Important Serverless Constraints
- **Database:** Serverless functions have ephemeral filesystems. While Demo Mode can run in-memory/temp storage for previewing, a hosted multi-user deployment **must** set `DATABASE_URL` to a hosted **Supabase PostgreSQL** database so data persists across requests.
- **Upload Limits & Timeouts:** `MAX_UPLOAD_SIZE_MB` defaults to `10 MB` and `vercel.json` sets `maxDuration: 30` seconds.
- **Environment Variables to configure in Vercel Project Settings:**
  - `APP_MODE=connected`
  - `DATABASE_URL=postgresql+psycopg://...`
  - `REQUIRE_AUTH=true`
  - `SUPABASE_JWT_SECRET=...` (or `APP_ACCESS_TOKEN=...`)
  - `GEMINI_API_KEY=...` (optional)

---

## 12. Common Errors & How to Fix Them

- **`Ambiguous date '04/05/2026' on row ...`**
  - **Cause:** Both the first and second parts of the date are `≤ 12`, and the date format selector is set to `Auto-detect`.
  - **Fix:** Select **`DD/MM/YYYY (Indian Standard)`** or **`MM/DD/YYYY`** in the Import Data preview and click **Re-validate Mapping**.
- **`Missing required column mapping(s)`**
  - **Cause:** Your CSV uses custom column headers that weren't auto-matched.
  - **Fix:** Use the dropdown menus under **2. Review Column Mappings** to pick the matching column from your CSV, then click **Re-validate Mapping**.
- **`401 Unauthorized` when calling APIs**
  - **Cause:** `REQUIRE_AUTH=true` is enabled in `.env`.
  - **Fix:** For local demo testing without tokens, set `REQUIRE_AUTH=false` in `.env` (or pass your configured `X-Ops-Token` header).

---

## 13. Privacy & Credential Handling

- **Never commit `.env` or real customer exports:** `.gitignore` blocks `.env`, `.env.*`, `data/`, and `uploads/`.
- **Phone Number Masking:** Customer phone numbers are masked (`+91 •••••• 2334`) across all tables and CSV exports.
- **CSV Formula Injection Protection:** All exported CSV cells starting with `=`, `+`, `-`, `@`, tab, or carriage return are prefixed with `'` so Microsoft Excel and Google Sheets cannot execute injected formulas.
- **Untrusted Input Isolation:** Customer complaint text is HTML-escaped in the UI and is never passed to an LLM or SQL interpreter.

---

## 14. Founder & Cashflow Command Center (`6. Founder & Cashflow Hub`)

Built specifically for Indian D2C founders and finance/ops leads, the **Founder & Cashflow Hub** (`GET /api/founder/dashboard`) computes real-time unit economics and COD cashflow health directly from reconciled workspace records using integer **paise** accounting:

1. **Gross-to-Net Contribution Margin Waterfall:**
   - Tracks **Gross GMV** → **Cancelled** → **Active GMV** → **RTO Uncollected Value** → **Realized Revenue** → **Product COGS** → **Forward & Return Courier Freight + COD Fees + Packaging** → **Net Contribution Margin (INR & %)**.
2. **COD Remittance & Discrepancy Audit Ledger (`GET /api/founder/cod-export.csv`):**
   - Tracks every Cash-on-Delivery order across `OVERDUE_PAYOUT` (delivered past courier `T+4` settlement SLA without UTR payout), `HOLD_DISPUTED_DELIVERY` (courier marked Delivered & collected cash, but customer raised a non-receipt complaint), `SHORT_REMITTED` (excess volumetric weight slab deductions), `AT_RISK_NDR` (active failed delivery attempts), `LOST_TO_RTO`, and `SETTLED`.
3. **SKU Velocity, RTO % & Stockout Runway:**
   - Compares remaining warehouse inventory cover (in days) against supplier manufacturing lead times (`CRITICAL_STOCKOUT_RISK`, `REORDER_NOW`, `HEALTHY`, `OVERSTOCKED_CAPITAL`) and recommends purchase order quantities.
4. **Courier SLA, NDR & RTO Scorecard:**
   - Benchmarks **BlueDart, Delhivery, Ecom Express, Xpressbees, Shadowfax, and DTDC** on Delivery %, RTO %, Stale Shipments (`≥ 72h`), Average Transit Hours, Overdue COD Cash, and lane allocation advice.

---

## 15. Model Context Protocol (MCP) Server & Studio (`7. MCP Studio`)

Order Ops Console is **MCP-ready** out of the box (`protocolVersion: 2024-11-05`, JSON-RPC 2.0), allowing AI clients (**Antigravity**, **Claude Desktop**, **Cursor**, or custom agents) to query live order operations and founder financials safely:

- **Stdio Transport (`mcp_stdio.py`):**
  ```json
  {
    "mcpServers": {
      "order-ops-console": {
        "command": "python",
        "args": ["D:/Order Ops Console/mcp_stdio.py"],
        "cwd": "D:/Order Ops Console"
      }
    }
  }
  ```
- **HTTP JSON-RPC 2.0 Transport:** `POST http://127.0.0.1:8000/api/mcp`
- **Interactive Web Playground (`7. MCP Studio`):** Browse and execute all **8 MCP Tools** (`get_founder_daily_brief`, `audit_cod_remittances`, `analyze_unit_economics`, `check_sku_inventory_runway`, `compare_courier_performance`, `search_flagged_orders`, `inspect_order_provenance`, `run_natural_language_ops_query`), **4 MCP Resources**, and **3 MCP Prompts** directly in the browser while inspecting raw JSON-RPC 2.0 request and response envelopes.

