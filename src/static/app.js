/* ==========================================================================
   Order Ops Console — Frontend Application Logic (Vanilla JS)
   ========================================================================== */

(function () {
  "use strict";

  const state = {
    activeTab: "overview",
    overview: null,
    ordersPage: 1,
    ordersPageSize: 25,
    ordersTotalPages: 1,
    specialFilters: {
      rule_code: "",
      fulfillment_status: "",
      min_stale_days: null,
      min_unshipped_days: null,
      label: "",
    },
    currentPreview: null,
    currentUploadFile: null,
  };

  function escapeHtml(value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function showToast(message, type = "info") {
    const container = document.getElementById("toast-container");
    if (!container) return;
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
      toast.remove();
    }, 4200);
  }

  async function apiFetch(url, options = {}) {
    const response = await fetch(url, options);
    if (!response.ok) {
      let errMsg = `Request failed (${response.status})`;
      try {
        const errData = await response.json();
        if (errData && errData.detail) {
          errMsg = typeof errData.detail === "string" ? errData.detail : JSON.stringify(errData.detail);
        }
      } catch (_e) {
        // ignore JSON parse error
      }
      throw new Error(errMsg);
    }
    return response.json();
  }

  function attentionBadgeHtml(level) {
    if (level === "High") return `<span class="badge badge-high">High Priority</span>`;
    if (level === "Medium") return `<span class="badge badge-medium">Medium Priority</span>`;
    if (level === "Low") return `<span class="badge badge-low">Low Priority</span>`;
    return `<span class="badge badge-ok">No Current Flag</span>`;
  }

  function shipmentStatusBadgeHtml(statusNorm, statusLabel) {
    const label = escapeHtml(statusLabel || statusNorm || "—");
    if (statusNorm === "delivered") return `<span class="badge badge-ok">${label}</span>`;
    if (statusNorm === "failed_attempt" || statusNorm === "rto_initiated" || statusNorm === "rto_delivered") {
      return `<span class="badge badge-high">${label}</span>`;
    }
    if (statusNorm === "in_transit" || statusNorm === "out_for_delivery" || statusNorm === "awaiting_pickup") {
      return `<span class="badge badge-low">${label}</span>`;
    }
    return `<span class="badge badge-neutral">${label}</span>`;
  }

  function matchBadgeHtml(matchStatus) {
    const label = escapeHtml(matchStatus || "—");
    if (matchStatus === "Exact match" || matchStatus === "Manually confirmed") {
      return `<span class="badge badge-ok">${label}</span>`;
    }
    if (matchStatus === "Strong match") {
      return `<span class="badge badge-low">${label}</span>`;
    }
    if (matchStatus === "Needs review") {
      return `<span class="badge badge-medium">${label}</span>`;
    }
    return `<span class="badge badge-neutral">${label}</span>`;
  }

  /* ========================================================================
     Tab Navigation
     ======================================================================== */
  function switchTab(tabName) {
    state.activeTab = tabName;
    document.querySelectorAll(".nav-tab").forEach((btn) => {
      const t = btn.getAttribute("data-tab");
      btn.classList.toggle("active", t === tabName || (tabName === "order-details" && t === "orders"));
    });
    document.querySelectorAll(".tab-panel").forEach((panel) => {
      panel.classList.toggle("active", panel.id === `tab-${tabName}`);
    });

    if (tabName === "overview") {
      loadOverview();
    } else if (tabName === "orders") {
      loadOrders();
    } else if (tabName === "match-review") {
      loadMatchReview();
    }
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  /* ========================================================================
     Overview Page
     ======================================================================== */
  async function loadOverview() {
    try {
      const data = await apiFetch("/api/overview");
      state.overview = data;

      // Header & Banner
      const modeBadge = document.getElementById("mode-badge");
      if (data.app_mode === "connected") {
        modeBadge.textContent = "Connected Mode (PostgreSQL)";
        modeBadge.className = "badge badge-ok";
      } else {
        modeBadge.textContent = "Demo Mode (Local SQLite)";
        modeBadge.className = "badge badge-medium";
      }

      document.getElementById("header-ref-time").textContent = data.reference_time_ist;
      document.getElementById("banner-ref-time").textContent = data.reference_time_ist;
      document.getElementById("overview-last-import").textContent = data.last_imported_at_ist;

      const demoBanner = document.getElementById("demo-banner");
      demoBanner.classList.toggle("hidden", !data.is_demo_dataset);

      // Nav counts
      const c = data.cards;
      document.getElementById("nav-badge-orders").textContent = c.total_orders;
      document.getElementById("nav-badge-review").textContent = c.unmatched_source_records;

      // Empty state vs Summary cards
      const emptyState = document.getElementById("overview-empty-state");
      emptyState.classList.toggle("hidden", c.total_orders > 0);

      document.getElementById("card-total-orders").textContent = c.total_orders;
      document.getElementById("card-awaiting-shipment").textContent = c.orders_awaiting_shipment;
      document.getElementById("card-in-transit").textContent = c.orders_in_transit;
      document.getElementById("card-stale-tracking").textContent = c.orders_with_stale_tracking;
      document.getElementById("card-stale-meta").textContent =
        `No scan for ≥ ${data.thresholds.stale_tracking_threshold_days} days (${data.thresholds.stale_tracking_threshold_hours}h) →`;
      document.getElementById("card-failed-attempts").textContent = c.orders_with_failed_delivery_attempts;
      document.getElementById("card-rto").textContent = c.orders_returning_to_origin;
      document.getElementById("card-open-complaints").textContent = c.open_customer_complaints;
      document.getElementById("card-needing-review").textContent = c.orders_needing_review;
      document.getElementById("card-priority-breakdown").textContent =
        `${c.high_priority_orders} High · ${c.medium_priority_orders} Med · ${c.low_priority_orders} Low →`;
      document.getElementById("card-unmatched-records").textContent = c.unmatched_source_records;
      document.getElementById("card-unmatched-meta").textContent =
        `${c.ambiguous_source_records} ambiguous · ${c.strictly_unmatched_records} unmatched →`;

      // Threshold inputs
      document.getElementById("input-unshipped-hours").value = data.thresholds.unshipped_threshold_hours;
      document.getElementById("input-stale-hours").value = data.thresholds.stale_tracking_threshold_hours;
      document.getElementById("select-ref-time-mode").value = data.reference_time_mode;

      // Environment Status
      document.getElementById("status-app-mode").textContent =
        data.is_demo_dataset ? "Demo Mode (Synthetic Dataset Active)" : "Custom Data Workspace";
      document.getElementById("status-db-backend").textContent = data.database_backend;
      document.getElementById("status-gemini-engine").textContent = data.gemini_model;

      // Populate filter dropdowns on Orders page
      populateFilterDropdowns(data.filter_options.couriers, data.filter_options.cities);

      // Recent Import Batches table
      renderRecentBatches(data.recent_batches);
    } catch (err) {
      showToast(`Failed to load overview: ${err.message}`, "error");
    }
  }

  function populateFilterDropdowns(couriers, cities) {
    const courierSel = document.getElementById("filter-courier");
    const currentCourier = courierSel.value;
    courierSel.innerHTML = `<option value="">All Couriers</option>` +
      couriers.map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");
    courierSel.value = currentCourier;

    const citySel = document.getElementById("filter-city");
    const currentCity = citySel.value;
    citySel.innerHTML = `<option value="">All Cities</option>` +
      cities.map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");
    citySel.value = currentCity;
  }

  function renderRecentBatches(batches) {
    const tbody = document.getElementById("overview-batches-tbody");
    if (!batches || batches.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" class="text-center text-muted">No batches imported yet. Click "Load Demo Data" above to populate 240 orders.</td></tr>`;
      return;
    }

    const sourceLabels = {
      shopify_orders: "Shopify Orders",
      courier_tracking: "Courier Tracking",
      whatsapp_complaints: "WhatsApp Complaints",
    };

    tbody.innerHTML = batches
      .map((b) => {
        const errCount = (b.rejected_rows || 0) + (b.duplicate_rows || 0);
        const errLink =
          errCount > 0
            ? `<a href="/api/import/batches/${b.id}/error-report.csv" class="btn btn-sm btn-ghost" download>⬇ Errors (${errCount})</a>`
            : `<span class="text-muted">0</span>`;
        return `
          <tr>
            <td>#${b.id}</td>
            <td><span class="badge badge-neutral">${escapeHtml(sourceLabels[b.source_type] || b.source_type)}</span></td>
            <td><strong>${escapeHtml(b.filename)}</strong></td>
            <td>${b.accepted_rows} / ${b.total_rows}</td>
            <td>${b.rejected_rows} rej · ${b.duplicate_rows} dup</td>
            <td>${escapeHtml(b.imported_at_ist)}</td>
            <td>
              ${errLink}
              <button type="button" class="btn btn-sm btn-ghost" data-delete-batch="${b.id}" title="Delete this import batch">
                Delete
              </button>
            </td>
          </tr>
        `;
      })
      .join("");
  }

  /* ========================================================================
     Overview Card Click -> Filtered Orders Navigation
     ======================================================================== */
  function resetAllOrderFilters() {
    document.getElementById("filter-search").value = "";
    document.getElementById("filter-attention").value = "";
    document.getElementById("filter-shipment-status").value = "";
    document.getElementById("filter-courier").value = "";
    document.getElementById("filter-payment").value = "";
    document.getElementById("filter-complaints").value = "";
    document.getElementById("filter-city").value = "";
    document.getElementById("filter-match-status").value = "";
    document.getElementById("filter-date-from").value = "";
    document.getElementById("filter-date-to").value = "";
    document.getElementById("filter-sort").value = "priority";

    state.specialFilters = {
      rule_code: "",
      fulfillment_status: "",
      min_stale_days: null,
      min_unshipped_days: null,
      label: "",
    };
    state.ordersPage = 1;
    updateActiveRuleBanner();
  }

  function updateActiveRuleBanner() {
    const banner = document.getElementById("active-rule-banner");
    const textEl = document.getElementById("active-rule-text");
    if (state.specialFilters.label) {
      textEl.textContent = `Active Filter: ${state.specialFilters.label}`;
      banner.classList.remove("hidden");
    } else {
      banner.classList.add("hidden");
    }
  }

  function handleSummaryCardClick(action) {
    if (action === "unmatched-records") {
      document.getElementById("review-status-filter").value = "actionable";
      switchTab("match-review");
      return;
    }

    resetAllOrderFilters();

    if (action === "awaiting-shipment") {
      document.getElementById("filter-shipment-status").value = "awaiting_shipment";
      state.specialFilters.label = "Orders Awaiting Shipment (Unfulfilled & Not Cancelled)";
    } else if (action === "in-transit") {
      document.getElementById("filter-shipment-status").value = "in_transit";
      state.specialFilters.label = "Orders Currently in Transit";
    } else if (action === "stale-tracking") {
      state.specialFilters.rule_code = "STALE_TRACKING";
      state.specialFilters.label = "Orders with Stale Tracking (No Courier Scan Past Threshold)";
    } else if (action === "failed-attempts") {
      document.getElementById("filter-shipment-status").value = "failed_attempt";
      state.specialFilters.label = "Orders with Failed Delivery Attempts (NDR)";
    } else if (action === "rto") {
      document.getElementById("filter-shipment-status").value = "rto";
      state.specialFilters.label = "Orders Returning to Origin (RTO)";
    } else if (action === "open-complaints") {
      document.getElementById("filter-complaints").value = "true";
      state.specialFilters.label = "Orders with Open Customer Complaints";
    } else if (action === "needing-review") {
      document.getElementById("filter-attention").value = "flagged_any";
      state.specialFilters.label = "All Orders with Operational Attention Flags";
    }

    updateActiveRuleBanner();
    switchTab("orders");
  }

  /* ========================================================================
     Orders Page & Filtering
     ======================================================================== */
  function buildOrdersQueryString(includePagination = true) {
    const params = new URLSearchParams();
    const search = document.getElementById("filter-search").value.trim();
    const attention = document.getElementById("filter-attention").value;
    const shpStatus = document.getElementById("filter-shipment-status").value;
    const courier = document.getElementById("filter-courier").value;
    const payment = document.getElementById("filter-payment").value;
    const complaints = document.getElementById("filter-complaints").value;
    const city = document.getElementById("filter-city").value;
    const matchStatus = document.getElementById("filter-match-status").value;
    const dateFrom = document.getElementById("filter-date-from").value;
    const dateTo = document.getElementById("filter-date-to").value;
    const sortBy = document.getElementById("filter-sort").value;

    if (search) params.set("search", search);
    if (attention) params.set("attention_level", attention);
    if (shpStatus) params.set("shipment_status", shpStatus);
    if (courier) params.set("courier", courier);
    if (payment) params.set("payment_method", payment);
    if (complaints) params.set("has_open_complaints", complaints);
    if (city) params.set("city", city);
    if (matchStatus) params.set("match_status", matchStatus);
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);
    if (sortBy) params.set("sort_by", sortBy);

    if (state.specialFilters.rule_code) {
      params.set("rule_code", state.specialFilters.rule_code);
    }
    if (state.specialFilters.fulfillment_status) {
      params.set("fulfillment_status", state.specialFilters.fulfillment_status);
    }
    if (state.specialFilters.min_stale_days) {
      params.set("min_stale_days", String(state.specialFilters.min_stale_days));
    }
    if (state.specialFilters.min_unshipped_days) {
      params.set("min_unshipped_days", String(state.specialFilters.min_unshipped_days));
    }

    if (includePagination) {
      params.set("page", String(state.ordersPage));
      params.set("page_size", String(state.ordersPageSize));
    }
    return params.toString();
  }

  async function loadOrders() {
    const tbody = document.getElementById("orders-tbody");
    try {
      const qs = buildOrdersQueryString(true);
      const data = await apiFetch(`/api/orders?${qs}`);

      state.ordersTotalPages = data.total_pages || 1;
      state.ordersPage = data.page || 1;

      document.getElementById("orders-Showing-count").textContent =
        `Showing ${data.items.length} of ${data.total} matching order(s)`;
      document.getElementById("orders-page-indicator").textContent =
        `Page ${state.ordersPage} of ${state.ordersTotalPages}`;
      document.getElementById("btn-prev-page").disabled = state.ordersPage <= 1;
      document.getElementById("btn-next-page").disabled = state.ordersPage >= state.ordersTotalPages;

      if (!data.items || data.items.length === 0) {
        tbody.innerHTML = `
          <tr>
            <td colspan="14" class="text-center text-muted" style="padding: 2rem;">
              No orders match the current filters. Try clicking "Reset Filters" or "Load Demo Data".
            </td>
          </tr>
        `;
        return;
      }

      tbody.innerHTML = data.items
        .map((r) => {
          const complaintBadge =
            r.open_complaint_count > 0
              ? `<span class="badge badge-high">${r.open_complaint_count} Open</span>`
              : r.complaint_count > 0
              ? `<span class="badge badge-neutral">${r.complaint_count} Resolved</span>`
              : `<span class="text-muted">0</span>`;

          return `
            <tr data-order-id="${r.id}" tabindex="0" role="button" aria-label="View details for Order ${escapeHtml(r.order_number)}">
              <td><strong>${escapeHtml(r.order_number)}</strong></td>
              <td>${escapeHtml(r.order_date_short)}</td>
              <td>${escapeHtml(r.customer_name)}</td>
              <td><code>${escapeHtml(r.customer_phone_masked)}</code></td>
              <td>${escapeHtml(r.city)}</td>
              <td><strong>${escapeHtml(r.order_value_inr)}</strong></td>
              <td>
                <span class="badge ${r.payment_method === "COD" ? "badge-medium" : "badge-neutral"}">
                  ${escapeHtml(r.payment_method)}
                </span>
              </td>
              <td>${escapeHtml(r.fulfillment_status)}</td>
              <td>
                <div><strong>${escapeHtml(r.courier)}</strong></div>
                <div class="text-muted" style="font-size: 0.76rem;">${escapeHtml(r.tracking_number)}</div>
              </td>
              <td>${shipmentStatusBadgeHtml(r.latest_shipment_status, r.latest_shipment_status_label)}</td>
              <td>${escapeHtml(r.last_tracking_update_ist)}</td>
              <td>${complaintBadge}</td>
              <td style="max-width: 280px;">
                <div>${attentionBadgeHtml(r.attention_level)}</div>
                <div class="text-muted" style="font-size: 0.76rem; margin-top: 0.2rem;">
                  ${escapeHtml(r.reason_for_attention)}
                </div>
              </td>
              <td>${matchBadgeHtml(r.match_status)}</td>
            </tr>
          `;
        })
        .join("");
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="14" class="text-center text-muted">Error loading orders: ${escapeHtml(err.message)}</td></tr>`;
    }
  }

  /* ========================================================================
     Order Details Page
     ======================================================================== */
  async function openOrderDetails(orderId) {
    try {
      const data = await apiFetch(`/api/orders/${orderId}`);
      const o = data.order;

      document.getElementById("detail-order-title").textContent =
        `Order ${o.order_number} — ${o.customer_name} (${o.city})`;
      document.getElementById("detail-order-subtitle").textContent =
        `Placed on ${o.order_date_ist} · Source: ${o.source_filename} (Row ${o.source_row_number}) · Reference Clock: ${data.reference_time_ist}`;

      document.getElementById("detail-header-badges").innerHTML = `
        ${attentionBadgeHtml(o.attention_level)}
        ${shipmentStatusBadgeHtml(o.latest_shipment_status, o.latest_shipment_status_label)}
        ${matchBadgeHtml(o.match_status)}
      `;

      // 1. Suggested Next Actions
      const actionsHtml =
        data.suggested_actions && data.suggested_actions.length > 0
          ? `
            <div class="card-panel" style="border-left: 4px solid #2563eb; background: #f8fafc;">
              <div class="panel-header" style="margin-bottom: 0.4rem;">
                <h3>Suggested Next Actions for Operations</h3>
                <span class="badge badge-neutral">Suggestions Only — Manual Execution</span>
              </div>
              <p class="text-muted" style="font-size: 0.78rem; margin-bottom: 0.6rem;">
                Order Ops Console does not automatically contact customers, message couriers, cancel orders, or issue refunds.
              </p>
              <ul style="margin: 0; padding-left: 1.25rem;">
                ${data.suggested_actions.map((act) => `<li style="margin-bottom: 0.35rem;"><strong>${escapeHtml(act)}</strong></li>`).join("")}
              </ul>
            </div>
          `
          : "";

      // 2. Attention Flags & Explanations
      const flagsHtml =
        data.attention_flags && data.attention_flags.length > 0
          ? data.attention_flags
              .map((f) => {
                const refsText = (f.source_references || [])
                  .map((r) => `${escapeHtml(r.source)} (Row ${escapeHtml(r.row)})`)
                  .join(", ");
                return `
                  <div class="flag-card flag-${escapeHtml(f.attention_level)}">
                    <div class="flag-header">
                      <strong>${escapeHtml(f.rule_name)} (${escapeHtml(f.rule_code)})</strong>
                      ${attentionBadgeHtml(f.attention_level)}
                    </div>
                    <p style="margin-bottom: 0.35rem;"><strong>Why flagged:</strong> ${escapeHtml(f.explanation)}</p>
                    <p class="text-muted" style="font-size: 0.82rem;"><strong>Triggering Evidence:</strong> ${escapeHtml(f.triggering_evidence)}</p>
                    <div class="flag-meta-grid">
                      <div><strong>Threshold Used:</strong> ${escapeHtml(f.threshold_used)}</div>
                      <div><strong>Source Provenance:</strong> ${refsText || "—"}</div>
                      <div><strong>Suggested Step:</strong> ${escapeHtml(f.suggested_action)}</div>
                    </div>
                  </div>
                `;
              })
              .join("")
          : `<p class="text-muted">No active attention flags for this order.</p>`;

      // 3. Candidate / Ambiguous Review Records Alert
      const candidateReviewHtml =
        data.candidate_review_records && data.candidate_review_records.length > 0
          ? `
            <div class="alert-box alert-warn">
              <strong>Ambiguous Source Records Need Review:</strong>
              ${data.candidate_review_records.length} record(s) in Match Review list this order as a candidate:
              <ul style="margin: 0.4rem 0;">
                ${data.candidate_review_records
                  .map(
                    (cr) =>
                      `<li><strong>${escapeHtml(cr.source_filename)} (Row ${cr.row_number}):</strong> ${escapeHtml(cr.explanation)}</li>`
                  )
                  .join("")}
              </ul>
              <button type="button" class="btn btn-sm btn-primary" data-goto-tab="match-review">
                Open Match Review Queue →
              </button>
            </div>
          `
          : "";

      // 4. Shipments Section (Supports multiple shipments per order)
      const shipmentsHtml =
        data.shipments && data.shipments.length > 0
          ? data.shipments
              .map(
                (s) => `
                  <div style="border: 1px solid var(--border-color); border-radius: 6px; padding: 0.85rem; margin-bottom: 0.75rem;">
                    <div class="panel-header" style="margin-bottom: 0.5rem;">
                      <div>
                        <strong>${escapeHtml(s.courier_name)} — AWB: <code>${escapeHtml(s.tracking_number)}</code></strong>
                        <div class="text-muted" style="font-size: 0.78rem;">
                          Source: ${escapeHtml(s.source_filename)} (Row ${s.source_row_number}) ·
                          Match: <strong>${escapeHtml(s.match_status)}</strong> (${escapeHtml(s.match_explanation)})
                        </div>
                      </div>
                      ${shipmentStatusBadgeHtml(s.current_status_norm, s.current_status_label)}
                    </div>
                    <div class="flag-meta-grid" style="margin-bottom: 0.65rem;">
                      <div><strong>Latest Hub:</strong> ${escapeHtml(s.latest_location)}</div>
                      <div><strong>Last Scan (IST):</strong> ${escapeHtml(s.latest_event_at_ist)}</div>
                      <div><strong>Expected Delivery:</strong> ${escapeHtml(s.expected_delivery_ist)}</div>
                      <div><strong>Failed Attempts:</strong> ${s.failed_attempts_count}</div>
                    </div>
                    ${
                      s.has_conflicting_status
                        ? `<div class="alert-box alert-danger" style="margin-bottom: 0.65rem;"><strong>Contradictory Scan History:</strong> ${escapeHtml(s.conflict_explanation)}</div>`
                        : ""
                    }
                    <div class="table-responsive">
                      <table class="data-table">
                        <thead>
                          <tr>
                            <th>Scan Time (IST)</th>
                            <th>Courier Status</th>
                            <th>Normalized Stage</th>
                            <th>Hub / Location</th>
                            <th>Remarks</th>
                            <th>Source File &amp; Row</th>
                          </tr>
                        </thead>
                        <tbody>
                          ${s.events
                            .map(
                              (ev) => `
                                <tr>
                                  <td>${escapeHtml(ev.event_at_ist)}</td>
                                  <td><strong>${escapeHtml(ev.status_raw)}</strong></td>
                                  <td>${escapeHtml(ev.status_label)}</td>
                                  <td>${escapeHtml(ev.location)}</td>
                                  <td>${escapeHtml(ev.remarks)}</td>
                                  <td>${escapeHtml(ev.source_filename)} (Row ${ev.source_row_number})</td>
                                </tr>
                              `
                            )
                            .join("")}
                        </tbody>
                      </table>
                    </div>
                  </div>
                `
              )
              .join("")
          : `<p class="text-muted">No courier shipments linked to this order yet.</p>`;

      // 5. Complaints Section
      const complaintsHtml =
        data.complaints && data.complaints.length > 0
          ? data.complaints
              .map(
                (c) => `
                  <div style="border: 1px solid var(--border-color); border-left: 4px solid #dc2626; border-radius: 6px; padding: 0.85rem; margin-bottom: 0.65rem;">
                    <div class="panel-header" style="margin-bottom: 0.35rem;">
                      <strong>Ticket ${escapeHtml(c.complaint_ref)} — ${escapeHtml(c.issue_category)}</strong>
                      <span class="badge ${c.status === "Open" ? "badge-high" : "badge-neutral"}">${escapeHtml(c.status)}</span>
                    </div>
                    <p style="margin-bottom: 0.45rem; font-size: 0.92rem;">"${escapeHtml(c.message_text)}"</p>
                    <div class="text-muted" style="font-size: 0.78rem;">
                      Received: ${escapeHtml(c.complaint_at_ist)} · Sender: ${escapeHtml(c.customer_name)} (${escapeHtml(c.customer_phone_masked)}) ·
                      Channel: ${escapeHtml(c.channel)}
                    </div>
                    <div class="text-muted" style="font-size: 0.78rem; margin-top: 0.2rem;">
                      <strong>Match Provenance:</strong> ${escapeHtml(c.match_status)} — ${escapeHtml(c.match_explanation)}
                      (${escapeHtml(c.source_filename)}, Row ${c.source_row_number})
                    </div>
                  </div>
                `
              )
              .join("")
          : `<p class="text-muted">No customer complaints attached to this order.</p>`;

      // 6. Chronological Event Timeline
      const timelineHtml =
        data.timeline && data.timeline.length > 0
          ? `
            <div class="timeline-list">
              ${data.timeline
                .map(
                  (t) => `
                    <div class="timeline-item">
                      <div class="timeline-top">
                        <strong>${escapeHtml(t.title)}</strong>
                        <span class="badge badge-neutral">${escapeHtml(t.timestamp_ist)}</span>
                      </div>
                      <p style="margin-bottom: 0.25rem;">${escapeHtml(t.description)}</p>
                      <div class="text-muted" style="font-size: 0.75rem;">
                        Source: ${escapeHtml(t.badge)} · ${escapeHtml(t.source_reference)}
                      </div>
                    </div>
                  `
                )
                .join("")}
            </div>
          `
          : `<p class="text-muted">No timeline events recorded.</p>`;

      const container = document.getElementById("order-details-content");
      container.innerHTML = `
        ${actionsHtml}
        ${candidateReviewHtml}

        <div class="card-panel">
          <div class="panel-header">
            <h3>Attention Flags &amp; Operational Explanations (${data.attention_flags.length})</h3>
          </div>
          ${flagsHtml}
        </div>

        <div class="details-two-col">
          <div class="card-panel">
            <h3>Order Summary &amp; Source Provenance</h3>
            <dl class="status-list" style="margin-top: 0.75rem;">
              <div class="status-item">
                <dt>Customer &amp; Masked Phone</dt>
                <dd>${escapeHtml(o.customer_name)} · <code>${escapeHtml(o.customer_phone_masked)}</code> · ${escapeHtml(o.customer_email)}</dd>
              </div>
              <div class="status-item">
                <dt>Shipping Destination</dt>
                <dd>${escapeHtml(o.city)}, ${escapeHtml(o.state)} — PIN: ${escapeHtml(o.pincode)}</dd>
              </div>
              <div class="status-item">
                <dt>Order Total &amp; Payment Method</dt>
                <dd>${escapeHtml(o.order_value_inr)} (${escapeHtml(o.payment_method)} · Financial status: ${escapeHtml(o.financial_status)})</dd>
              </div>
              <div class="status-item">
                <dt>Fulfillment &amp; Promised Delivery</dt>
                <dd>Fulfillment: <strong>${escapeHtml(o.fulfillment_status)}</strong> · Promised Delivery: ${escapeHtml(o.promised_delivery_ist)}</dd>
              </div>
              <div class="status-item">
                <dt>Reconciliation &amp; Source File</dt>
                <dd>${matchBadgeHtml(o.match_status)} — ${escapeHtml(o.source_filename)} (Row ${o.source_row_number})</dd>
              </div>
            </dl>
          </div>

          <div class="card-panel">
            <h3>Consolidated Line Items (${data.line_items.length})</h3>
            <div class="table-responsive" style="margin-top: 0.65rem;">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>SKU</th>
                    <th>Product</th>
                    <th>Qty</th>
                    <th>Unit Price</th>
                    <th>Status</th>
                    <th>Source Row</th>
                  </tr>
                </thead>
                <tbody>
                  ${data.line_items
                    .map(
                      (li) => `
                        <tr>
                          <td><code>${escapeHtml(li.sku)}</code></td>
                          <td><strong>${escapeHtml(li.product_title)}</strong></td>
                          <td>${li.quantity}</td>
                          <td>${escapeHtml(li.unit_price_inr)}</td>
                          <td>${escapeHtml(li.fulfillment_status)}</td>
                          <td>${escapeHtml(li.source_filename)} #${li.source_row_number}</td>
                        </tr>
                      `
                    )
                    .join("")}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <div class="card-panel">
          <div class="panel-header">
            <h3>Associated Shipment Records (${data.shipments.length})</h3>
          </div>
          ${shipmentsHtml}
        </div>

        <div class="details-two-col">
          <div class="card-panel">
            <div class="panel-header">
              <h3>Customer Complaints (${data.complaints.length})</h3>
            </div>
            ${complaintsHtml}
          </div>

          <div class="card-panel">
            <div class="panel-header">
              <h3>Chronological Event Timeline</h3>
            </div>
            ${timelineHtml}
          </div>
        </div>
      `;

      switchTab("order-details");
    } catch (err) {
      showToast(`Could not open order details: ${err.message}`, "error");
    }
  }

  /* ========================================================================
     Import Data Page
     ======================================================================== */
  async function runCsvPreview(useCustomMapping = false) {
    const fileInput = document.getElementById("import-file-input");
    const sourceType = document.getElementById("import-source-type").value;
    const datePref = document.getElementById("import-date-format").value;

    const file = fileInput.files && fileInput.files[0] ? fileInput.files[0] : state.currentUploadFile;
    if (!file) {
      showToast("Please select a .csv file to preview.", "error");
      return;
    }
    state.currentUploadFile = file;

    const formData = new FormData();
    formData.append("file", file);
    formData.append("source_type", sourceType);
    formData.append("date_format_preference", datePref);

    if (useCustomMapping) {
      const customMap = collectCurrentMappingFromSelects();
      formData.append("custom_mapping", JSON.stringify(customMap));
    }

    try {
      const data = await apiFetch("/api/import/preview", {
        method: "POST",
        body: formData,
      });
      state.currentPreview = data;
      renderImportPreview(data);
    } catch (err) {
      showToast(`Preview failed: ${err.message}`, "error");
    }
  }

  function collectCurrentMappingFromSelects() {
    const mapping = {};
    document.querySelectorAll(".mapping-select").forEach((sel) => {
      const fieldKey = sel.getAttribute("data-field");
      if (sel.value) {
        mapping[fieldKey] = sel.value;
      }
    });
    return mapping;
  }

  function renderImportPreview(data) {
    const container = document.getElementById("import-preview-container");
    container.classList.remove("hidden");
    document.getElementById("import-summary-container").classList.add("hidden");

    document.getElementById("preview-file-meta").textContent =
      `File: ${data.filename} · Encoding: ${data.encoding_used} · Date Format Mode: ${data.date_format_preference}`;

    document.getElementById("val-total").textContent = data.counts.total_rows;
    document.getElementById("val-accepted").textContent = data.counts.accepted_rows;
    document.getElementById("val-rejected").textContent = data.counts.rejected_rows;
    document.getElementById("val-duplicate").textContent = data.counts.duplicate_rows;

    // Missing required fields alert
    const missingAlert = document.getElementById("preview-missing-alert");
    if (data.missing_required_fields && data.missing_required_fields.length > 0) {
      missingAlert.innerHTML =
        `<strong>Missing Required Column Mapping(s):</strong> ` +
        data.missing_required_fields.map((f) => escapeHtml(f.label)).join(", ") +
        `. Please map the required field(s) below and click "Re-validate Mapping".`;
      missingAlert.classList.remove("hidden");
    } else {
      missingAlert.classList.add("hidden");
    }

    // Ambiguous dates alert
    const ambigAlert = document.getElementById("preview-ambiguous-alert");
    if (data.ambiguous_date_samples && data.ambiguous_date_samples.length > 0) {
      const sampleList = data.ambiguous_date_samples
        .map((s) => `Row ${s.row_number} (${escapeHtml(s.field)}: <code>${escapeHtml(s.raw_value)}</code>)`)
        .join(", ");
      ambigAlert.innerHTML = `
        <div>
          <strong>Ambiguous Date Format Detected:</strong> ${sampleList}.
          Dates where both numbers are ≤ 12 are never guessed silently. Choose the expected format to resolve:
        </div>
        <div style="margin-top: 0.5rem; display: flex; gap: 0.5rem; flex-wrap: wrap;">
          <button type="button" class="btn btn-sm btn-primary" data-resolve-date-fmt="DD/MM/YYYY">
            Use DD/MM/YYYY (Indian Standard)
          </button>
          <button type="button" class="btn btn-sm btn-secondary" data-resolve-date-fmt="MM/DD/YYYY">
            Use MM/DD/YYYY (Month First)
          </button>
        </div>
      `;
      ambigAlert.classList.remove("hidden");
    } else {
      ambigAlert.classList.add("hidden");
    }

    // Render mapping selects
    const mappingGrid = document.getElementById("mapping-grid");
    mappingGrid.innerHTML = data.field_options
      .map((f) => {
        const currentHeader = data.suggested_mapping[f.field] || "";
        const reqBadge = f.required ? `<span class="badge badge-high" style="margin-left: 4px;">Required</span>` : "";
        const optionsHtml =
          `<option value="">— Not Mapped —</option>` +
          data.headers
            .map(
              (h) =>
                `<option value="${escapeHtml(h)}" ${h === currentHeader ? "selected" : ""}>${escapeHtml(h)}</option>`
            )
            .join("");
        return `
          <div class="form-group">
            <label>${escapeHtml(f.label)} ${reqBadge}</label>
            <select class="mapping-select" data-field="${escapeHtml(f.field)}">
              ${optionsHtml}
            </select>
          </div>
        `;
      })
      .join("");

    // Render sample rows
    const sampleTable = document.getElementById("preview-sample-table");
    const thead = sampleTable.querySelector("thead");
    const tbody = sampleTable.querySelector("tbody");
    const cols = data.headers.slice(0, 8);
    thead.innerHTML =
      `<tr><th>Row #</th>` + cols.map((h) => `<th>${escapeHtml(h)}</th>`).join("") + `</tr>`;
    tbody.innerHTML = (data.sample_rows || [])
      .slice(0, 5)
      .map(
        (r) =>
          `<tr><td>#${r.row_number}</td>` +
          cols.map((h) => `<td>${escapeHtml(r.data[h] || "")}</td>`).join("") +
          `</tr>`
      )
      .join("");

    // Render rejected / duplicate rows
    const errSection = document.getElementById("preview-errors-section");
    const errBtn = document.getElementById("btn-download-preview-errors");
    const errTbody = document.getElementById("preview-errors-tbody");

    if (data.error_rows && data.error_rows.length > 0) {
      errSection.classList.remove("hidden");
      errBtn.classList.remove("hidden");
      errTbody.innerHTML = data.error_rows
        .map(
          (er) => `
            <tr>
              <td>Row #${er.row_number}</td>
              <td>
                <span class="badge ${er.status === "rejected" ? "badge-high" : "badge-medium"}">
                  ${escapeHtml(er.status)}
                </span>
              </td>
              <td>${escapeHtml(er.reasons)}</td>
            </tr>
          `
        )
        .join("");
    } else {
      errSection.classList.add("hidden");
      errBtn.classList.add("hidden");
    }

    document.getElementById("btn-commit-import").disabled = !data.can_import;
  }

  async function commitCsvImport() {
    if (!state.currentUploadFile || !state.currentPreview) return;
    const sourceType = document.getElementById("import-source-type").value;
    let datePref = document.getElementById("import-date-format").value;
    if (datePref === "auto") {
      datePref = "DD/MM/YYYY";
    }
    const customMap = collectCurrentMappingFromSelects();

    const formData = new FormData();
    formData.append("file", state.currentUploadFile);
    formData.append("source_type", sourceType);
    formData.append("date_format_preference", datePref);
    formData.append("custom_mapping", JSON.stringify(customMap));

    try {
      const summary = await apiFetch("/api/import/commit", {
        method: "POST",
        body: formData,
      });

      const summaryContainer = document.getElementById("import-summary-container");
      summaryContainer.classList.remove("hidden");
      summaryContainer.innerHTML = `
        <div class="panel-header">
          <div>
            <h3>Import Completed &amp; Reconciled (#${summary.batch_id})</h3>
            <p class="text-muted">Source file <strong>${escapeHtml(summary.filename)}</strong> has been normalized and reconciled.</p>
          </div>
          <div class="section-actions">
            ${
              summary.rejected_rows + summary.duplicate_rows > 0
                ? `<a href="/api/import/batches/${summary.batch_id}/error-report.csv" class="btn btn-sm btn-secondary" download>⬇ Download Error Report (.csv)</a>`
                : ""
            }
            <button type="button" class="btn btn-primary" data-goto-tab="orders">
              View Reconciled Orders →
            </button>
          </div>
        </div>
        <div class="validation-stats-row">
          <div class="val-stat val-stat-ok">
            <span>Accepted Rows</span>
            <strong>${summary.accepted_rows}</strong>
          </div>
          <div class="val-stat val-stat-err">
            <span>Rejected Rows</span>
            <strong>${summary.rejected_rows}</strong>
          </div>
          <div class="val-stat val-stat-dup">
            <span>Duplicates Skipped</span>
            <strong>${summary.duplicate_rows}</strong>
          </div>
          <div class="val-stat">
            <span>Flagged Orders Now</span>
            <strong>${summary.reconciliation.flagged_orders}</strong>
          </div>
          <div class="val-stat">
            <span>Needs Match Review</span>
            <strong>${summary.reconciliation.needs_review_records}</strong>
          </div>
        </div>
      `;

      showToast(`Imported ${summary.accepted_rows} row(s) from ${summary.filename}.`, "success");
      loadOverview();
    } catch (err) {
      showToast(`Import failed: ${err.message}`, "error");
    }
  }

  /* ========================================================================
     Match Review Page
     ======================================================================== */
  async function loadMatchReview() {
    const statusFilter = document.getElementById("review-status-filter").value;
    const sourceFilter = document.getElementById("review-source-filter").value;
    const listContainer = document.getElementById("match-review-list");

    try {
      const data = await apiFetch(
        `/api/match-review?status_filter=${encodeURIComponent(statusFilter)}&source_filter=${encodeURIComponent(sourceFilter)}`
      );

      document.getElementById("rev-count-needs-review").textContent = data.counts.needs_review;
      document.getElementById("rev-count-unmatched").textContent = data.counts.unmatched;
      document.getElementById("rev-count-manual").textContent = data.counts.manually_confirmed;
      document.getElementById("nav-badge-review").textContent =
        data.counts.needs_review + data.counts.unmatched;

      const orderOptionsHtml =
        `<option value="">Select an Order to Link / Override…</option>` +
        (data.available_orders || [])
          .map(
            (o) =>
              `<option value="${o.id}">${escapeHtml(o.order_number)} — ${escapeHtml(o.customer_name)} (${escapeHtml(o.city)}, ${escapeHtml(o.order_value_inr)})</option>`
          )
          .join("");

      if (!data.items || data.items.length === 0) {
        listContainer.innerHTML = `
          <div class="empty-state-card">
            <h3>No Records in This Review Filter</h3>
            <p>All records matching this filter have been resolved or none are pending.</p>
          </div>
        `;
      } else {
        listContainer.innerHTML = data.items
          .map((item) => {
            const statusClass = item.reconciliation_status.split(" ")[0];
            const rawPairs = Object.entries(item.raw_payload || {})
              .filter(([_k, v]) => v !== "")
              .map(([k, v]) => `<span style="margin-right: 0.85rem;"><strong>${escapeHtml(k)}:</strong> ${escapeHtml(v)}</span>`)
              .join("");

            const currentMatchLine = item.matched_order
              ? `<div class="alert-box" style="background: var(--ok-bg); border: 1px solid var(--ok-border); color: var(--ok-text); margin-top: 0.5rem;">
                   Currently linked to <strong>Order ${escapeHtml(item.matched_order.order_number)}</strong>
                   (${escapeHtml(item.matched_order.customer_name)}, ${escapeHtml(item.matched_order.city)}).
                 </div>`
              : "";

            const candidatesHtml =
              item.candidates && item.candidates.length > 0
                ? `<div class="candidate-list">
                     ${item.candidates
                       .map(
                         (c) => `
                           <div class="candidate-box">
                             <div>
                               <strong>Suggested Order ${escapeHtml(c.order_number)}</strong> —
                               ${escapeHtml(c.customer_name)} (${escapeHtml(c.city)} · ${escapeHtml(c.total_amount_inr)} · ${escapeHtml(c.payment_method)})
                               <span class="badge badge-neutral" style="margin-left: 0.4rem;">Heuristic Score: ${c.heuristic_score}/100</span>
                               <div class="text-muted" style="font-size: 0.78rem; margin-top: 0.2rem;">
                                 <strong>Why suggested:</strong> ${(c.reasons || []).map(escapeHtml).join(" · ")}
                               </div>
                             </div>
                             <div style="display: flex; gap: 0.45rem; flex-wrap: wrap;">
                               <button
                                 type="button"
                                 class="btn btn-sm btn-primary"
                                 data-match-decide="confirm_match"
                                 data-raw-id="${item.raw_record_id}"
                                 data-order-id="${c.order_id}"
                               >
                                 ✓ Confirm Match to ${escapeHtml(c.order_number)}
                               </button>
                               <button
                                 type="button"
                                 class="btn btn-sm btn-secondary"
                                 data-open-order="${c.order_id}"
                               >
                                 Inspect Order
                               </button>
                             </div>
                           </div>
                         `
                       )
                       .join("")}
                   </div>`
                : `<p class="text-muted" style="margin-top: 0.5rem;">No automatic candidate suggestions met the minimum threshold. You can leave this record unmatched or manually assign it to an order below.</p>`;

            return `
              <div class="review-card status-${escapeHtml(statusClass)}">
                <div class="panel-header" style="margin-bottom: 0.35rem;">
                  <div>
                    <strong>${escapeHtml(item.record_title)}</strong>
                    <span class="text-muted" style="font-size: 0.78rem; margin-left: 0.5rem;">
                      Source: ${escapeHtml(item.source_filename)} (Row ${item.row_number})
                    </span>
                  </div>
                  ${matchBadgeHtml(item.reconciliation_status)}
                </div>

                <p style="margin-bottom: 0.4rem;">${escapeHtml(item.record_subtitle)}</p>
                <p style="font-size: 0.82rem; color: var(--med-text); margin-bottom: 0.45rem;">
                  <strong>Reconciliation Assessment:</strong> ${escapeHtml(item.match_explanation)}
                </p>

                <div style="background: var(--bg-subtle); padding: 0.5rem 0.75rem; border-radius: 4px; font-size: 0.77rem; color: var(--text-secondary);">
                  <strong>Raw Imported Fields (Preserved):</strong> ${rawPairs}
                </div>

                ${currentMatchLine}
                ${candidatesHtml}

                <div style="display: flex; align-items: center; justify-content: space-between; gap: 0.75rem; flex-wrap: wrap; margin-top: 0.85rem; padding-top: 0.75rem; border-top: 1px solid var(--border-color);">
                  <div style="display: flex; align-items: center; gap: 0.45rem; flex-wrap: wrap;">
                    <select id="manual-order-select-${item.raw_record_id}" aria-label="Select order for manual override">
                      ${orderOptionsHtml}
                    </select>
                    <button
                      type="button"
                      class="btn btn-sm btn-secondary"
                      data-match-manual-select="${item.raw_record_id}"
                    >
                      Assign / Override Match
                    </button>
                  </div>

                  <div style="display: flex; gap: 0.45rem;">
                    <button
                      type="button"
                      class="btn btn-sm btn-ghost"
                      data-match-decide="reject_candidate"
                      data-raw-id="${item.raw_record_id}"
                    >
                      Reject Suggestion / Leave Unmatched
                    </button>
                  </div>
                </div>
              </div>
            `;
          })
          .join("");
      }

      // Render audit history
      const auditTbody = document.getElementById("match-audit-tbody");
      if (!data.audit_history || data.audit_history.length === 0) {
        auditTbody.innerHTML = `<tr><td colspan="8" class="text-center text-muted">No manual decisions recorded yet. Confirm or reject a candidate above to record an audit entry.</td></tr>`;
      } else {
        auditTbody.innerHTML = data.audit_history
          .map(
            (d) => `
              <tr>
                <td>${escapeHtml(d.decided_at_ist)}</td>
                <td>Record #${d.raw_record_id} (${escapeHtml(d.source_type)})</td>
                <td><code>${escapeHtml(d.decision_type)}</code></td>
                <td>${escapeHtml(d.previous_status)}</td>
                <td>${matchBadgeHtml(d.new_status)}</td>
                <td><strong>${escapeHtml(d.selected_order_number || "Unmatched")}</strong></td>
                <td>${escapeHtml(d.note)}</td>
                <td>${escapeHtml(d.decided_by)}</td>
              </tr>
            `
          )
          .join("");
      }
    } catch (err) {
      showToast(`Failed to load match review queue: ${err.message}`, "error");
    }
  }

  async function submitMatchDecision(rawRecordId, decisionType, selectedOrderId = null, note = "") {
    try {
      const res = await apiFetch("/api/match-review/decide", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          raw_record_id: Number(rawRecordId),
          decision_type: decisionType,
          selected_order_id: selectedOrderId ? Number(selectedOrderId) : null,
          note: note,
        }),
      });
      showToast(
        res.selected_order_number
          ? `Confirmed match to Order ${res.selected_order_number}.`
          : `Record #${rawRecordId} marked as Unmatched.`,
        "success"
      );
      loadMatchReview();
    } catch (err) {
      showToast(`Failed to save decision: ${err.message}`, "error");
    }
  }

  /* ========================================================================
     Ask Your Orders Page
     ======================================================================== */
  async function submitNaturalLanguageQuestion(questionText) {
    const input = document.getElementById("input-ask-question");
    input.value = questionText;
    const container = document.getElementById("ask-response-container");
    container.classList.remove("hidden");
    container.innerHTML = `<div class="card-panel"><p class="text-muted">Evaluating question against order records…</p></div>`;

    try {
      const data = await apiFetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: questionText }),
      });

      renderAskResponse(data);
    } catch (err) {
      container.innerHTML = `<div class="alert-box alert-danger">Error: ${escapeHtml(err.message)}</div>`;
    }
  }

  function renderAskResponse(data) {
    const container = document.getElementById("ask-response-container");

    // 1. Ambiguous Question -> Offer explicit choices
    if (data.is_ambiguous) {
      const choicesHtml = (data.clarification_choices || [])
        .map(
          (ch) => `
            <button
              type="button"
              class="clarification-card"
              data-question="${escapeHtml(ch.follow_up_question)}"
            >
              <strong style="display: block; color: var(--brand-primary); font-size: 0.92rem; margin-bottom: 0.25rem;">
                ${escapeHtml(ch.label)} →
              </strong>
              <span class="text-muted" style="font-size: 0.82rem;">${escapeHtml(ch.description)}</span>
            </button>
          `
        )
        .join("");

      container.innerHTML = `
        <div class="card-panel" style="border-left: 4px solid #d97706;">
          <div class="panel-header">
            <div>
              <h3>Clarification Needed — Choose Your Definition</h3>
              <p class="text-muted">Reference Time: ${escapeHtml(data.reference_time_ist)} · Parser: ${escapeHtml(data.parser_used)}</p>
            </div>
            <span class="badge badge-medium">Ambiguous Term</span>
          </div>
          <p>${escapeHtml(data.explanation)}</p>
          <div class="clarification-grid">
            ${choicesHtml}
          </div>
        </div>
      `;
      return;
    }

    // 2. Unsupported Question
    if (data.is_unsupported) {
      container.innerHTML = `
        <div class="card-panel" style="border-left: 4px solid #64748b;">
          <div class="panel-header">
            <h3>Unsupported Request</h3>
            <span class="badge badge-neutral">Guardrail Active</span>
          </div>
          <p>${escapeHtml(data.explanation)}</p>
        </div>
      `;
      return;
    }

    // 3. Validated Filter Results
    const filtersEntries = Object.entries(data.filters_applied || {});
    const filtersHtml =
      filtersEntries.length > 0
        ? filtersEntries
            .map(
              ([k, v]) =>
                `<span class="badge badge-neutral" style="margin-right: 0.4rem;"><strong>${escapeHtml(k)}:</strong>&nbsp;${escapeHtml(v)}</span>`
            )
            .join("")
        : `<span class="badge badge-neutral">All Records</span>`;

    const openActionBtn = data.open_in_match_review
      ? `<button type="button" class="btn btn-sm btn-primary" data-goto-tab="match-review">
           Open in Match Review Queue (${(data.unmatched_source_records || []).length} records) →
         </button>`
      : `<button type="button" class="btn btn-sm btn-primary" id="btn-open-ask-in-orders">
           Open ${data.total_results} Result(s) in Orders Page →
         </button>`;

    const rowsHtml =
      data.results && data.results.length > 0
        ? data.results
            .map(
              (r) => `
                <tr data-order-id="${r.id}" style="cursor: pointer;">
                  <td><strong>${escapeHtml(r.order_number)}</strong></td>
                  <td>${escapeHtml(r.order_date_short)}</td>
                  <td>${escapeHtml(r.customer_name)}</td>
                  <td>${escapeHtml(r.city)}</td>
                  <td>${escapeHtml(r.order_value_inr)} (${escapeHtml(r.payment_method)})</td>
                  <td>${escapeHtml(r.courier)} · <code>${escapeHtml(r.tracking_number)}</code></td>
                  <td>${shipmentStatusBadgeHtml(r.latest_shipment_status, r.latest_shipment_status_label)}</td>
                  <td>${attentionBadgeHtml(r.attention_level)}</td>
                  <td style="font-size: 0.78rem;">${escapeHtml(r.reason_for_attention)}</td>
                </tr>
              `
            )
            .join("")
        : `<tr><td colspan="9" class="text-center text-muted">No matching orders found.</td></tr>`;

    container.innerHTML = `
      <div class="card-panel">
        <div class="panel-header">
          <div>
            <h3>Answer (${data.total_results} Matching Order${data.total_results === 1 ? "" : "s"})</h3>
            <p style="margin-top: 0.2rem;">${escapeHtml(data.explanation)}</p>
          </div>
          ${openActionBtn}
        </div>

        <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 0.85rem; padding: 0.55rem 0.75rem; background: var(--bg-subtle); border-radius: 6px;">
          <div><strong>Filters Used:</strong> ${filtersHtml}</div>
          <div class="text-muted" style="font-size: 0.78rem;">
            <strong>Reference Time:</strong> ${escapeHtml(data.reference_time_ist)} ·
            <strong>Engine:</strong> ${escapeHtml(data.parser_used)}
          </div>
        </div>

        <div class="table-responsive">
          <table class="data-table orders-table">
            <thead>
              <tr>
                <th>Order #</th>
                <th>Order Date</th>
                <th>Customer</th>
                <th>City</th>
                <th>Value &amp; Payment</th>
                <th>Courier &amp; AWB</th>
                <th>Shipment Status</th>
                <th>Priority</th>
                <th>Explanation</th>
              </tr>
            </thead>
            <tbody>
              ${rowsHtml}
            </tbody>
          </table>
        </div>
      </div>
    `;

    const openOrdersBtn = document.getElementById("btn-open-ask-in-orders");
    if (openOrdersBtn) {
      openOrdersBtn.addEventListener("click", () => {
        applyFilterParamsAndOpenOrders(data.orders_page_filter_params || {}, data.explanation);
      });
    }
  }

  function applyFilterParamsAndOpenOrders(params, explanationLabel) {
    resetAllOrderFilters();
    if (params.shipment_status) {
      document.getElementById("filter-shipment-status").value = params.shipment_status;
    }
    if (params.payment_method) {
      document.getElementById("filter-payment").value = params.payment_method;
    }
    if (params.courier) {
      document.getElementById("filter-courier").value = params.courier;
    }
    if (params.city) {
      document.getElementById("filter-city").value = params.city;
    }
    if (params.attention_level) {
      document.getElementById("filter-attention").value = params.attention_level;
    }
    if (params.has_open_complaints) {
      document.getElementById("filter-complaints").value = params.has_open_complaints;
    }
    if (params.match_status) {
      document.getElementById("filter-match-status").value = params.match_status;
    }
    if (params.rule_code) {
      state.specialFilters.rule_code = params.rule_code;
    }
    if (params.fulfillment_status) {
      state.specialFilters.fulfillment_status = params.fulfillment_status;
    }
    if (params.min_stale_days) {
      state.specialFilters.min_stale_days = params.min_stale_days;
    }
    if (params.min_unshipped_days) {
      state.specialFilters.min_unshipped_days = params.min_unshipped_days;
    }
    state.specialFilters.label = explanationLabel || "Filtered from Ask Your Orders";
    updateActiveRuleBanner();
    switchTab("orders");
  }

  /* ========================================================================
     Walkthrough Helper Actions
     ======================================================================== */
  async function handleWalkthroughStep(stepKey) {
    if (stepKey === "stale-order") {
      resetAllOrderFilters();
      document.getElementById("filter-search").value = "#01004";
      const res = await apiFetch("/api/orders?search=%2301004");
      if (res.items && res.items.length > 0) {
        openOrderDetails(res.items[0].id);
      }
    } else if (stepKey === "dnr-order") {
      resetAllOrderFilters();
      document.getElementById("filter-search").value = "#01015";
      const res = await apiFetch("/api/orders?search=%2301015");
      if (res.items && res.items.length > 0) {
        openOrderDetails(res.items[0].id);
      }
    } else if (stepKey === "multi-shipment") {
      resetAllOrderFilters();
      document.getElementById("filter-search").value = "#01009";
      const res = await apiFetch("/api/orders?search=%2301009");
      if (res.items && res.items.length > 0) {
        openOrderDetails(res.items[0].id);
      }
    } else if (stepKey === "match-review") {
      document.getElementById("review-status-filter").value = "actionable";
      switchTab("match-review");
    } else if (stepKey === "ask-stuck") {
      switchTab("ask");
      submitNaturalLanguageQuestion("Which orders are stuck?");
    }
  }

  /* ========================================================================
     Event Listeners Initialization
     ======================================================================== */
  function initEventListeners() {
    // Primary Nav Tabs
    document.querySelectorAll(".nav-tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        switchTab(btn.getAttribute("data-tab"));
      });
    });

    // Load Demo Data buttons
    async function triggerLoadDemo() {
      const btn = document.getElementById("btn-load-demo");
      btn.disabled = true;
      btn.textContent = "Loading Demo…";
      try {
        const res = await apiFetch("/api/demo/load", { method: "POST" });
        showToast(
          `Loaded synthetic demo dataset: ${res.reconciliation.orders_total} orders reconciled!`,
          "success"
        );
        document.getElementById("walkthrough-panel").classList.remove("hidden");
        await loadOverview();
        if (state.activeTab === "orders") loadOrders();
        if (state.activeTab === "match-review") loadMatchReview();
      } catch (err) {
        showToast(`Failed to load demo data: ${err.message}`, "error");
      } finally {
        btn.disabled = false;
        btn.textContent = "Load Demo Data";
      }
    }

    document.getElementById("btn-load-demo").addEventListener("click", triggerLoadDemo);
    document.getElementById("btn-empty-load-demo").addEventListener("click", triggerLoadDemo);

    // Clear Workspace Data
    document.getElementById("btn-clear-data").addEventListener("click", async () => {
      try {
        await apiFetch("/api/demo/clear", { method: "POST" });
        showToast("Workspace cleared.", "info");
        loadOverview();
      } catch (err) {
        showToast(`Failed to clear workspace: ${err.message}`, "error");
      }
    });

    // Walkthrough toggle
    document.getElementById("btn-toggle-walkthrough").addEventListener("click", () => {
      document.getElementById("walkthrough-panel").classList.toggle("hidden");
    });
    document.getElementById("btn-close-walkthrough").addEventListener("click", () => {
      document.getElementById("walkthrough-panel").classList.add("hidden");
    });
    document.querySelectorAll(".walkthrough-step").forEach((btn) => {
      btn.addEventListener("click", () => {
        handleWalkthroughStep(btn.getAttribute("data-walkthrough"));
      });
    });

    // Re-run Reconciliation
    document.getElementById("btn-reconcile-now").addEventListener("click", async () => {
      try {
        const res = await apiFetch("/api/reconcile", { method: "POST" });
        showToast(
          `Reconciliation complete: ${res.flagged_orders} flagged orders, ${res.needs_review_records} records need review.`,
          "success"
        );
        loadOverview();
      } catch (err) {
        showToast(`Reconcile error: ${err.message}`, "error");
      }
    });

    // Thresholds Panel
    document.getElementById("btn-open-thresholds").addEventListener("click", () => {
      document.getElementById("thresholds-panel").classList.toggle("hidden");
    });
    document.getElementById("btn-close-thresholds").addEventListener("click", () => {
      document.getElementById("thresholds-panel").classList.add("hidden");
    });
    document.getElementById("form-thresholds").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const unshippedH = Number(document.getElementById("input-unshipped-hours").value);
        const staleH = Number(document.getElementById("input-stale-hours").value);
        const refMode = document.getElementById("select-ref-time-mode").value;
        await apiFetch("/api/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            unshipped_threshold_hours: unshippedH,
            stale_tracking_threshold_hours: staleH,
            reference_time_mode: refMode,
          }),
        });
        showToast("Thresholds updated and all order flags re-evaluated.", "success");
        loadOverview();
      } catch (err) {
        showToast(`Failed to update settings: ${err.message}`, "error");
      }
    });

    // Overview Summary Cards Click
    document.querySelectorAll(".summary-card").forEach((card) => {
      card.addEventListener("click", () => {
        handleSummaryCardClick(card.getAttribute("data-card-action"));
      });
    });

    // Import Page Events
    document.getElementById("form-import-upload").addEventListener("submit", (e) => {
      e.preventDefault();
      runCsvPreview(false);
    });
    document.getElementById("btn-revalidate-mapping").addEventListener("click", () => {
      runCsvPreview(true);
    });
    document.getElementById("btn-commit-import").addEventListener("click", () => {
      commitCsvImport();
    });
    document.getElementById("btn-download-preview-errors").addEventListener("click", async () => {
      if (!state.currentPreview || !state.currentPreview.error_rows) return;
      const resp = await fetch("/api/import/preview-error-report.csv", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filename: state.currentPreview.filename,
          error_rows: state.currentPreview.error_rows,
        }),
      });
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `error_report_${state.currentPreview.filename}`;
      a.click();
      URL.revokeObjectURL(url);
    });

    // Orders Filters & Pagination
    let searchTimer = null;
    document.getElementById("filter-search").addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        state.ordersPage = 1;
        loadOrders();
      }, 220);
    });

    [
      "filter-attention",
      "filter-shipment-status",
      "filter-courier",
      "filter-payment",
      "filter-complaints",
      "filter-city",
      "filter-match-status",
      "filter-date-from",
      "filter-date-to",
      "filter-sort",
    ].forEach((id) => {
      document.getElementById(id).addEventListener("change", () => {
        state.ordersPage = 1;
        loadOrders();
      });
    });

    document.getElementById("btn-reset-filters").addEventListener("click", () => {
      resetAllOrderFilters();
      loadOrders();
    });

    document.getElementById("btn-clear-rule-filter").addEventListener("click", () => {
      state.specialFilters = {
        rule_code: "",
        fulfillment_status: "",
        min_stale_days: null,
        min_unshipped_days: null,
        label: "",
      };
      updateActiveRuleBanner();
      loadOrders();
    });

    document.getElementById("btn-prev-page").addEventListener("click", () => {
      if (state.ordersPage > 1) {
        state.ordersPage -= 1;
        loadOrders();
      }
    });
    document.getElementById("btn-next-page").addEventListener("click", () => {
      if (state.ordersPage < state.ordersTotalPages) {
        state.ordersPage += 1;
        loadOrders();
      }
    });

    // Export Filtered CSV
    document.getElementById("btn-export-orders").addEventListener("click", () => {
      const qs = buildOrdersQueryString(false);
      window.location.href = `/api/orders/export.csv?${qs}`;
    });

    // Back to Orders from Order Details
    document.getElementById("btn-back-to-orders").addEventListener("click", () => {
      switchTab("orders");
    });

    // Match Review Filters
    document.getElementById("review-status-filter").addEventListener("change", loadMatchReview);
    document.getElementById("review-source-filter").addEventListener("change", loadMatchReview);

    // Ask Your Orders Form
    document.getElementById("form-ask-question").addEventListener("submit", (e) => {
      e.preventDefault();
      const q = document.getElementById("input-ask-question").value.trim();
      if (q) submitNaturalLanguageQuestion(q);
    });

    // Delegated Click Handler for Dynamic Elements
    document.body.addEventListener("click", async (e) => {
      const gotoBtn = e.target.closest("[data-goto-tab]");
      if (gotoBtn) {
        switchTab(gotoBtn.getAttribute("data-goto-tab"));
        return;
      }

      const orderRow = e.target.closest("tr[data-order-id]");
      if (orderRow) {
        openOrderDetails(orderRow.getAttribute("data-order-id"));
        return;
      }

      const openOrderBtn = e.target.closest("[data-open-order]");
      if (openOrderBtn) {
        openOrderDetails(openOrderBtn.getAttribute("data-open-order"));
        return;
      }

      const qBtn = e.target.closest("[data-question]");
      if (qBtn) {
        submitNaturalLanguageQuestion(qBtn.getAttribute("data-question"));
        return;
      }

      const dateFmtBtn = e.target.closest("[data-resolve-date-fmt]");
      if (dateFmtBtn) {
        const fmt = dateFmtBtn.getAttribute("data-resolve-date-fmt");
        document.getElementById("import-date-format").value = fmt;
        runCsvPreview(true);
        return;
      }

      const decideBtn = e.target.closest("[data-match-decide]");
      if (decideBtn) {
        const rawId = decideBtn.getAttribute("data-raw-id");
        const decisionType = decideBtn.getAttribute("data-match-decide");
        const orderId = decideBtn.getAttribute("data-order-id");
        submitMatchDecision(rawId, decisionType, orderId);
        return;
      }

      const manualSelectBtn = e.target.closest("[data-match-manual-select]");
      if (manualSelectBtn) {
        const rawId = manualSelectBtn.getAttribute("data-match-manual-select");
        const sel = document.getElementById(`manual-order-select-${rawId}`);
        if (!sel || !sel.value) {
          showToast("Please select an order from the dropdown first.", "error");
          return;
        }
        submitMatchDecision(rawId, "change_match", sel.value, "Manual order assignment in Match Review");
        return;
      }

      const delBatchBtn = e.target.closest("[data-delete-batch]");
      if (delBatchBtn) {
        const batchId = delBatchBtn.getAttribute("data-delete-batch");
        try {
          await apiFetch(`/api/import/batches/${batchId}`, { method: "DELETE" });
          showToast(`Deleted import batch #${batchId} and re-reconciled workspace.`, "info");
          loadOverview();
        } catch (err) {
          showToast(`Delete failed: ${err.message}`, "error");
        }
      }
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    initEventListeners();
    loadOverview();
  });
})();
