-- Runs AFTER Phinx (console.alert_thresholds, just added). Same company_isolation shape
-- as every other console.* table in 002_rls_policies.sql (that file predates this table).
-- Unlike egress_allowlist, company_id here is NOT nullable (always written as exactly one
-- row per company by SettingsController's upsert), so no NULL-row carve-out is needed.

ALTER TABLE console.alert_thresholds ENABLE ROW LEVEL SECURITY;
ALTER TABLE console.alert_thresholds FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS company_isolation ON console.alert_thresholds;
CREATE POLICY company_isolation ON console.alert_thresholds
  USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
  WITH CHECK (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid);
DROP POLICY IF EXISTS platform_admin_read_all ON console.alert_thresholds;
CREATE POLICY platform_admin_read_all ON console.alert_thresholds
  FOR SELECT TO console_platform USING (true);

ALTER TABLE console.alert_thresholds OWNER TO db_owner;
GRANT SELECT, INSERT, UPDATE, DELETE ON console.alert_thresholds TO console_app, console_platform;
