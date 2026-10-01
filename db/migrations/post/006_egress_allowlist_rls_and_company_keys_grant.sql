-- Runs AFTER both Phinx (console.egress_allowlist, just added) and Alembic
-- (runtime.company_keys, already exists) migrations. PRD-001 §7.3/§7.7/§8.1.

-- console.egress_allowlist (PRD §7.4/§8.1) - same company_isolation shape as every other
-- console.* table in 002_rls_policies.sql (that file predates this table, so it isn't
-- there yet). company_id is nullable for a future platform-wide entry (not written
-- through console-api's CRUD endpoints today - see the Phinx migration's own comment),
-- so the USING clause must not implicitly block a NULL-company row from being plainly
-- invisible to every company read - that is the intended behavior for now (platform-wide
-- rows are a later SafeHttpClient-side concern, not something any single company reads
-- through this policy).
ALTER TABLE console.egress_allowlist ENABLE ROW LEVEL SECURITY;
ALTER TABLE console.egress_allowlist FORCE ROW LEVEL SECURITY;
CREATE POLICY company_isolation ON console.egress_allowlist
  USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
  WITH CHECK (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid);
CREATE POLICY platform_admin_read_all ON console.egress_allowlist
  FOR SELECT TO console_platform USING (true);

ALTER TABLE console.egress_allowlist OWNER TO db_owner;
GRANT SELECT, INSERT, UPDATE, DELETE ON console.egress_allowlist TO console_app, console_platform;

-- runtime.company_keys (PRD §7.7/§12 "destroy-dek"/crypto-shredding) - console-api's
-- CompaniesController::destroyCompanyDek needs to UPDATE this ai/-owned table (zero the
-- wrapped_dek bytes, set destroyed_at) acting within the target company's own scope
-- (ControllerBase::runInTransactionAsCompany, same company_isolation policy ai/'s own
-- 0007 migration already created - no new policy needed here, just the grant console_app
-- never had). UPDATE's WHERE clause also needs SELECT on the referenced columns.
GRANT SELECT, UPDATE ON runtime.company_keys TO console_app;
