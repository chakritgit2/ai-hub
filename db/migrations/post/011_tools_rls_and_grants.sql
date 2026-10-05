-- console.tools didn't exist when 002_rls_policies.sql ran, so it gets the same bulk
-- treatment that file gave every other console.* table, in its own file — same reasoning
-- as 006_egress_allowlist_rls_and_company_keys_grant.sql did for egress_allowlist (PRD §7.3).

ALTER TABLE console.tools ENABLE ROW LEVEL SECURITY;
ALTER TABLE console.tools FORCE ROW LEVEL SECURITY;
CREATE POLICY company_isolation ON console.tools
  USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid);
CREATE POLICY platform_admin_read_all ON console.tools
  FOR SELECT TO console_platform USING (true);

ALTER TABLE console.tools OWNER TO db_owner;
GRANT SELECT, INSERT, UPDATE, DELETE ON console.tools TO console_app, console_platform;
