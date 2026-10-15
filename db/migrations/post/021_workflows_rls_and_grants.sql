-- console.workflows/console.workflow_versions didn't exist when 002_rls_policies.sql ran,
-- so they get the same bulk treatment that file gave every other console.* table, in
-- their own file - same precedent as 017_datasets_rls_and_grants.sql (PRD §6.9, §8.2).

ALTER TABLE console.workflows ENABLE ROW LEVEL SECURITY;
ALTER TABLE console.workflows FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS company_isolation ON console.workflows;
CREATE POLICY company_isolation ON console.workflows
  USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
  WITH CHECK (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid);
DROP POLICY IF EXISTS platform_admin_read_all ON console.workflows;
CREATE POLICY platform_admin_read_all ON console.workflows
  FOR SELECT TO console_platform USING (true);

ALTER TABLE console.workflows OWNER TO db_owner;
GRANT SELECT, INSERT, UPDATE, DELETE ON console.workflows TO console_app, console_platform;

ALTER TABLE console.workflow_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE console.workflow_versions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS company_isolation ON console.workflow_versions;
CREATE POLICY company_isolation ON console.workflow_versions
  USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
  WITH CHECK (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid);
DROP POLICY IF EXISTS platform_admin_read_all ON console.workflow_versions;
CREATE POLICY platform_admin_read_all ON console.workflow_versions
  FOR SELECT TO console_platform USING (true);

ALTER TABLE console.workflow_versions OWNER TO db_owner;
GRANT SELECT, INSERT, UPDATE, DELETE ON console.workflow_versions TO console_app, console_platform;
