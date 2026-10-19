-- ============================================================================
-- Order Ops Console — Supabase PostgreSQL Schema & Row Level Security (RLS)
-- ============================================================================
-- Run this script in the Supabase SQL Editor when setting up Connected Mode.
-- Note: SQLAlchemy (`create_all_tables()`) automatically creates these tables
-- when connected via `DATABASE_URL`, and this script enables PostgreSQL
-- Row Level Security (RLS) so each authenticated user can only access rows
-- matching their isolated `workspace_id` (`'user-' || auth.uid()::text`).
-- ============================================================================

-- 1. Enable Row Level Security on all 10 application tables
ALTER TABLE IF EXISTS settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS import_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS raw_source_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS order_line_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS shipments ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS shipment_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS complaints ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS match_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS attention_flags ENABLE ROW LEVEL SECURITY;

-- 2. Helper function returning the expected workspace_id for the current Supabase Auth user
CREATE OR REPLACE FUNCTION public.current_user_workspace_id()
RETURNS text
LANGUAGE sql
STABLE
AS $$
  SELECT 'user-' || auth.uid()::text;
$$;

-- 3. Tenant Isolation Policies (Users can only SELECT/INSERT/UPDATE/DELETE their own workspace_id)
DROP POLICY IF EXISTS "workspace_isolation_settings" ON settings;
CREATE POLICY "workspace_isolation_settings" ON settings
  FOR ALL TO authenticated
  USING (workspace_id = public.current_user_workspace_id())
  WITH CHECK (workspace_id = public.current_user_workspace_id());

DROP POLICY IF EXISTS "workspace_isolation_import_batches" ON import_batches;
CREATE POLICY "workspace_isolation_import_batches" ON import_batches
  FOR ALL TO authenticated
  USING (workspace_id = public.current_user_workspace_id())
  WITH CHECK (workspace_id = public.current_user_workspace_id());

DROP POLICY IF EXISTS "workspace_isolation_raw_source_records" ON raw_source_records;
CREATE POLICY "workspace_isolation_raw_source_records" ON raw_source_records
  FOR ALL TO authenticated
  USING (workspace_id = public.current_user_workspace_id())
  WITH CHECK (workspace_id = public.current_user_workspace_id());

DROP POLICY IF EXISTS "workspace_isolation_orders" ON orders;
CREATE POLICY "workspace_isolation_orders" ON orders
  FOR ALL TO authenticated
  USING (workspace_id = public.current_user_workspace_id())
  WITH CHECK (workspace_id = public.current_user_workspace_id());

DROP POLICY IF EXISTS "workspace_isolation_order_line_items" ON order_line_items;
CREATE POLICY "workspace_isolation_order_line_items" ON order_line_items
  FOR ALL TO authenticated
  USING (workspace_id = public.current_user_workspace_id())
  WITH CHECK (workspace_id = public.current_user_workspace_id());

DROP POLICY IF EXISTS "workspace_isolation_shipments" ON shipments;
CREATE POLICY "workspace_isolation_shipments" ON shipments
  FOR ALL TO authenticated
  USING (workspace_id = public.current_user_workspace_id())
  WITH CHECK (workspace_id = public.current_user_workspace_id());

DROP POLICY IF EXISTS "workspace_isolation_shipment_events" ON shipment_events;
CREATE POLICY "workspace_isolation_shipment_events" ON shipment_events
  FOR ALL TO authenticated
  USING (workspace_id = public.current_user_workspace_id())
  WITH CHECK (workspace_id = public.current_user_workspace_id());

DROP POLICY IF EXISTS "workspace_isolation_complaints" ON complaints;
CREATE POLICY "workspace_isolation_complaints" ON complaints
  FOR ALL TO authenticated
  USING (workspace_id = public.current_user_workspace_id())
  WITH CHECK (workspace_id = public.current_user_workspace_id());

DROP POLICY IF EXISTS "workspace_isolation_match_decisions" ON match_decisions;
CREATE POLICY "workspace_isolation_match_decisions" ON match_decisions
  FOR ALL TO authenticated
  USING (workspace_id = public.current_user_workspace_id())
  WITH CHECK (workspace_id = public.current_user_workspace_id());

DROP POLICY IF EXISTS "workspace_isolation_attention_flags" ON attention_flags;
CREATE POLICY "workspace_isolation_attention_flags" ON attention_flags
  FOR ALL TO authenticated
  USING (workspace_id = public.current_user_workspace_id())
  WITH CHECK (workspace_id = public.current_user_workspace_id());
