-- console.datasets/console.dataset_items didn't exist when 002_rls_policies.sql ran,
-- so they get the same bulk treatment that file gave every other console.* table, in
-- their own file - same reasoning as 011_tools_rls_and_grants.sql/015_kb_rls_and_grants.sql
-- (PRD §7.3, §6.7, §8.2). dataset_items gets its own company_id + RLS policy even though
-- the PRD's §8.2 column list for it only names "dataset_id, question, expected_answer,
-- context, tags" - console.api_key_scopes' PRD row has the same abbreviation but its real
-- migration/RLS still carry company_id directly, so this follows that same established
-- precedent rather than a join-through-parent policy.

ALTER TABLE console.datasets ENABLE ROW LEVEL SECURITY;
ALTER TABLE console.datasets FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS company_isolation ON console.datasets;
CREATE POLICY company_isolation ON console.datasets
  USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
  WITH CHECK (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid);
DROP POLICY IF EXISTS platform_admin_read_all ON console.datasets;
CREATE POLICY platform_admin_read_all ON console.datasets
  FOR SELECT TO console_platform USING (true);

ALTER TABLE console.datasets OWNER TO db_owner;
GRANT SELECT, INSERT, UPDATE, DELETE ON console.datasets TO console_app, console_platform;

ALTER TABLE console.dataset_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE console.dataset_items FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS company_isolation ON console.dataset_items;
CREATE POLICY company_isolation ON console.dataset_items
  USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
  WITH CHECK (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid);
DROP POLICY IF EXISTS platform_admin_read_all ON console.dataset_items;
CREATE POLICY platform_admin_read_all ON console.dataset_items
  FOR SELECT TO console_platform USING (true);

ALTER TABLE console.dataset_items OWNER TO db_owner;
GRANT SELECT, INSERT, UPDATE, DELETE ON console.dataset_items TO console_app, console_platform;
