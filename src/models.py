"""Relational database models for Order Ops Console.

Entities:
1. ImportBatch - Metadata and validation summary for each uploaded CSV file.
2. RawSourceRecord - Immutable raw CSV row preserved separately from normalized records.
3. Order - Consolidated Shopify order record (money stored in integer minor units: paise).
4. OrderLineItem - Individual line items belonging to an Order.
5. Shipment - Courier shipment / AWB record (supports multiple shipments per order).
6. ShipmentEvent - Chronological tracking scan history for a shipment.
7. Complaint - Customer complaint imported from WhatsApp CSV logs.
8. MatchDecision - Audit trail of manual match confirmations, rejections, and overrides.
9. AttentionFlag - Operational rule evaluations explaining why an order needs attention.
10. OperationalSettings - Per-workspace thresholds and reference time configuration.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class OperationalSettings(Base):
    """Per-workspace configuration for thresholds, reference time, and demo status."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    reference_time_mode: Mapped[str] = mapped_column(String(32), default="demo_fixed", nullable=False)
    demo_reference_iso: Mapped[str] = mapped_column(
        String(64), default="2026-10-19T18:00:00+05:30", nullable=False
    )
    unshipped_threshold_hours: Mapped[int] = mapped_column(Integer, default=48, nullable=False)
    stale_tracking_threshold_hours: Mapped[int] = mapped_column(Integer, default=72, nullable=False)
    expected_delivery_grace_hours: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_demo_dataset: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class ImportBatch(Base):
    """Tracks each CSV upload or demo load batch."""

    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    date_format_preference: Mapped[str] = mapped_column(String(32), default="DD/MM/YYYY", nullable=False)
    total_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    accepted_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rejected_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    column_mapping_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    error_report_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    summary_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    raw_records: Mapped[list["RawSourceRecord"]] = relationship(
        "RawSourceRecord", back_populates="import_batch", cascade="all, delete-orphan"
    )


class Order(Base):
    """Consolidated normalized order record.

    Money is stored in integer minor units (`total_amount_paise`, where 1 INR = 100 paise)
    to prevent binary floating-point rounding bugs.
    """

    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("workspace_id", "order_number_norm", name="uq_orders_workspace_order_norm"),
        Index("ix_orders_workspace_phone", "workspace_id", "customer_phone_norm"),
        Index("ix_orders_workspace_attention", "workspace_id", "attention_level"),
        Index("ix_orders_workspace_date", "workspace_id", "order_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    import_batch_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("import_batches.id", ondelete="SET NULL"), nullable=True
    )
    raw_record_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    order_number_raw: Mapped[str] = mapped_column(String(128), nullable=False)
    order_number_norm: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    order_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    promised_delivery_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    customer_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    customer_phone_raw: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    customer_phone_norm: Mapped[str] = mapped_column(String(32), default="", index=True, nullable=False)
    customer_phone_masked: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    customer_email: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    city: Mapped[str] = mapped_column(String(128), default="", index=True, nullable=False)
    state: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    pincode: Mapped[str] = mapped_column(String(32), default="", nullable=False)

    total_amount_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(16), default="INR", nullable=False)
    payment_method: Mapped[str] = mapped_column(String(32), default="Prepaid", index=True, nullable=False)
    financial_status: Mapped[str] = mapped_column(String(64), default="paid", nullable=False)
    fulfillment_status: Mapped[str] = mapped_column(String(64), default="unfulfilled", index=True, nullable=False)
    is_cancelled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    source_filename: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Cached operational summary fields (recomputed deterministically during reconciliation)
    attention_level: Mapped[str] = mapped_column(String(32), default="No current flag", nullable=False)
    primary_attention_reason: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    match_status: Mapped[str] = mapped_column(String(64), default="Exact match", index=True, nullable=False)
    match_explanation: Mapped[str] = mapped_column(Text, default="", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    line_items: Mapped[list["OrderLineItem"]] = relationship(
        "OrderLineItem", back_populates="order", cascade="all, delete-orphan"
    )
    shipments: Mapped[list["Shipment"]] = relationship("Shipment", back_populates="order")
    complaints: Mapped[list["Complaint"]] = relationship("Complaint", back_populates="order")
    attention_flags: Mapped[list["AttentionFlag"]] = relationship(
        "AttentionFlag", back_populates="order", cascade="all, delete-orphan"
    )


class OrderLineItem(Base):
    """Individual SKU / product row within a Shopify order export."""

    __tablename__ = "order_line_items"
    __table_args__ = (
        UniqueConstraint("order_id", "line_item_key", name="uq_order_line_item_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("orders.id", ondelete="CASCADE"), index=True, nullable=False
    )
    raw_record_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    line_item_key: Mapped[str] = mapped_column(String(128), nullable=False)
    sku: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    product_title: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    unit_price_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fulfillment_status: Mapped[str] = mapped_column(String(64), default="unfulfilled", nullable=False)
    tracking_number_raw: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    courier_name: Mapped[str] = mapped_column(String(128), default="", nullable=False)

    source_filename: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    order: Mapped["Order"] = relationship("Order", back_populates="line_items")


class Shipment(Base):
    """Courier shipment identified by tracking number / AWB.

    One order may have multiple shipments (split fulfillment or re-shipment).
    """

    __tablename__ = "shipments"
    __table_args__ = (
        UniqueConstraint("workspace_id", "tracking_number_norm", name="uq_shipments_workspace_tracking"),
        Index("ix_shipments_workspace_status", "workspace_id", "current_status_norm"),
        Index("ix_shipments_workspace_courier", "workspace_id", "courier_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    order_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("orders.id", ondelete="SET NULL"), index=True, nullable=True
    )
    import_batch_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("import_batches.id", ondelete="SET NULL"), nullable=True
    )
    raw_record_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    tracking_number_raw: Mapped[str] = mapped_column(String(128), nullable=False)
    tracking_number_norm: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    courier_name: Mapped[str] = mapped_column(String(128), default="Unknown Courier", nullable=False)

    order_number_ref_raw: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    order_number_ref_norm: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    customer_phone_raw: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    customer_phone_norm: Mapped[str] = mapped_column(String(32), default="", nullable=False)

    current_status_raw: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    current_status_norm: Mapped[str] = mapped_column(String(64), default="unknown", nullable=False)
    latest_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latest_location: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    expected_delivery_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    failed_attempts_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    has_conflicting_status: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    conflict_explanation: Mapped[str] = mapped_column(Text, default="", nullable=False)

    match_status: Mapped[str] = mapped_column(String(64), default="Unmatched", nullable=False)
    match_method: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    match_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    match_explanation: Mapped[str] = mapped_column(Text, default="", nullable=False)

    source_filename: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    order: Mapped["Order | None"] = relationship("Order", back_populates="shipments")
    events: Mapped[list["ShipmentEvent"]] = relationship(
        "ShipmentEvent", back_populates="shipment", cascade="all, delete-orphan"
    )


class ShipmentEvent(Base):
    """Individual tracking scan or status event belonging to a Shipment."""

    __tablename__ = "shipment_events"
    __table_args__ = (
        UniqueConstraint("shipment_id", "event_key", name="uq_shipment_event_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    shipment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("shipments.id", ondelete="CASCADE"), index=True, nullable=False
    )
    raw_record_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    event_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status_raw: Mapped[str] = mapped_column(String(128), nullable=False)
    status_norm: Mapped[str] = mapped_column(String(64), nullable=False)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    location: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    remarks: Mapped[str] = mapped_column(Text, default="", nullable=False)
    delivery_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    source_filename: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    shipment: Mapped["Shipment"] = relationship("Shipment", back_populates="events")


class Complaint(Base):
    """Customer complaint imported from a WhatsApp complaint CSV log."""

    __tablename__ = "complaints"
    __table_args__ = (
        UniqueConstraint("workspace_id", "complaint_key", name="uq_complaints_workspace_key"),
        Index("ix_complaints_workspace_status", "workspace_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    order_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("orders.id", ondelete="SET NULL"), index=True, nullable=True
    )
    import_batch_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("import_batches.id", ondelete="SET NULL"), nullable=True
    )
    raw_record_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    complaint_key: Mapped[str] = mapped_column(String(128), nullable=False)
    complaint_ref: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    customer_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    customer_phone_raw: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    customer_phone_norm: Mapped[str] = mapped_column(String(32), default="", index=True, nullable=False)
    customer_phone_masked: Mapped[str] = mapped_column(String(32), default="", nullable=False)

    order_number_mentioned_raw: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    order_number_mentioned_norm: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    tracking_mentioned_norm: Mapped[str] = mapped_column(String(128), default="", nullable=False)

    complaint_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    channel: Mapped[str] = mapped_column(String(64), default="WhatsApp CSV Import", nullable=False)
    issue_category: Mapped[str] = mapped_column(String(128), default="Delivery Inquiry", nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="Open", nullable=False)
    claims_not_received: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    match_status: Mapped[str] = mapped_column(String(64), default="Unmatched", nullable=False)
    match_method: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    match_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    match_explanation: Mapped[str] = mapped_column(Text, default="", nullable=False)

    source_filename: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    order: Mapped["Order | None"] = relationship("Order", back_populates="complaints")


class RawSourceRecord(Base):
    """Immutable raw source row from an uploaded CSV, preserved separately from normalized tables."""

    __tablename__ = "raw_source_records"
    __table_args__ = (
        Index("ix_raw_records_workspace_reconcile", "workspace_id", "reconciliation_status"),
        Index("ix_raw_records_workspace_fingerprint", "workspace_id", "source_type", "row_fingerprint"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    import_batch_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("import_batches.id", ondelete="CASCADE"), index=True, nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    row_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)

    raw_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_payload_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)

    validation_status: Mapped[str] = mapped_column(String(32), default="accepted", nullable=False)
    validation_errors_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)

    reconciliation_status: Mapped[str] = mapped_column(String(64), default="Unmatched", nullable=False)
    matched_order_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("orders.id", ondelete="SET NULL"), index=True, nullable=True
    )
    match_method: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    match_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    match_explanation: Mapped[str] = mapped_column(Text, default="", nullable=False)
    candidate_matches_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    import_batch: Mapped["ImportBatch"] = relationship("ImportBatch", back_populates="raw_records")
    decisions: Mapped[list["MatchDecision"]] = relationship(
        "MatchDecision", back_populates="raw_record", cascade="all, delete-orphan"
    )


class MatchDecision(Base):
    """Audit trail of manual reconciliation decisions on ambiguous or unmatched source records."""

    __tablename__ = "match_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    raw_record_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("raw_source_records.id", ondelete="CASCADE"), index=True, nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_order_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    selected_order_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    decision_type: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_status: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    new_status: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    decided_by: Mapped[str] = mapped_column(String(128), default="Operations User", nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    raw_record: Mapped["RawSourceRecord"] = relationship("RawSourceRecord", back_populates="decisions")


class AttentionFlag(Base):
    """Explainable operational flag raised on an Order."""

    __tablename__ = "attention_flags"
    __table_args__ = (
        Index("ix_attention_flags_workspace_order", "workspace_id", "order_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    rule_code: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    rule_name: Mapped[str] = mapped_column(String(128), nullable=False)
    attention_level: Mapped[str] = mapped_column(String(32), nullable=False)
    triggering_evidence: Mapped[str] = mapped_column(Text, nullable=False)
    threshold_used: Mapped[str] = mapped_column(String(128), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_action: Mapped[str] = mapped_column(Text, nullable=False)
    source_references_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    order: Mapped["Order"] = relationship("Order", back_populates="attention_flags")
