-- Runs AFTER Phinx has created the console.* tables. PRD-001 §7.3.
-- Company isolation, layer 2: FORCE ROW LEVEL SECURITY on every table with company_id.
-- The company is set with SET LOCAL app.company_id inside the transaction only (required with
-- PgBouncer transaction pooling + PHP-FPM) — see console-api/app/services/CompanyContext.php.
-- Policies use current_setting(..., true): when unset, no rows are visible (fail-closed).

ALTER TABLE console.company_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE console.company_members FORCE ROW LEVEL SECURITY;
CREATE POLICY company_isolation ON console.company_members
  USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid);

ALTER TABLE console.connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE console.connections FORCE ROW LEVEL SECURITY;
CREATE POLICY company_isolation ON console.connections
  USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid);

ALTER TABLE console.agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE console.agents FORCE ROW LEVEL SECURITY;
CREATE POLICY company_isolation ON console.agents
  USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid);

ALTER TABLE console.agent_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE console.agent_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY company_isolation ON console.agent_versions
  USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid);

ALTER TABLE console.deployments ENABLE ROW LEVEL SECURITY;
ALTER TABLE console.deployments FORCE ROW LEVEL SECURITY;
CREATE POLICY company_isolation ON console.deployments
  USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid);

ALTER TABLE console.api_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE console.api_keys FORCE ROW LEVEL SECURITY;
CREATE POLICY company_isolation ON console.api_keys
  USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid);

-- audit_logs.company_id is nullable (platform-level events, e.g. an SSO login claiming an
-- unregistered company ref — PRD §12) — the WITH CHECK clause lets those rows insert with
-- no app.company_id set at all, but the USING clause still means a NULL-company row can
-- never match any company's app.company_id, so only platform_admin_read_all below can
-- ever read them back.
ALTER TABLE console.audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE console.audit_logs FORCE ROW LEVEL SECURITY;
CREATE POLICY company_isolation ON console.audit_logs
  USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
  WITH CHECK (company_id IS NULL OR company_id = NULLIF(current_setting('app.company_id', true), '')::uuid);

-- console_platform (platform_admin) gets an explicit cross-company read policy per table,
-- not BYPASSRLS (PRD §7.3).
CREATE POLICY platform_admin_read_all ON console.company_members
  FOR SELECT TO console_platform USING (true);
CREATE POLICY platform_admin_read_all ON console.connections
  FOR SELECT TO console_platform USING (true);
CREATE POLICY platform_admin_read_all ON console.agents
  FOR SELECT TO console_platform USING (true);
CREATE POLICY platform_admin_read_all ON console.agent_versions
  FOR SELECT TO console_platform USING (true);
CREATE POLICY platform_admin_read_all ON console.deployments
  FOR SELECT TO console_platform USING (true);
CREATE POLICY platform_admin_read_all ON console.api_keys
  FOR SELECT TO console_platform USING (true);
CREATE POLICY platform_admin_read_all ON console.audit_logs
  FOR SELECT TO console_platform USING (true);

-- App roles never own tables — ownership stays with db_owner (used only for migrations).
ALTER TABLE console.companies OWNER TO db_owner;
ALTER TABLE console.users OWNER TO db_owner;
ALTER TABLE console.company_members OWNER TO db_owner;
ALTER TABLE console.connections OWNER TO db_owner;
ALTER TABLE console.agents OWNER TO db_owner;
ALTER TABLE console.agent_versions OWNER TO db_owner;
ALTER TABLE console.deployments OWNER TO db_owner;
ALTER TABLE console.api_keys OWNER TO db_owner;
ALTER TABLE console.audit_logs OWNER TO db_owner;

GRANT SELECT, INSERT, UPDATE, DELETE ON
  console.companies, console.users, console.company_members, console.connections,
  console.agents, console.agent_versions, console.deployments, console.api_keys,
  console.audit_logs
TO console_app, console_platform;
