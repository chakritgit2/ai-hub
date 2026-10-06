-- console.knowledge_bases didn't exist when 002_rls_policies.sql ran, so it gets the same
-- bulk treatment that file gave every other console.* table, in its own file — same
-- reasoning as 011_tools_rls_and_grants.sql/013_skills_rls_and_grants.sql (PRD §7.3, §6.6).

ALTER TABLE console.knowledge_bases ENABLE ROW LEVEL SECURITY;
ALTER TABLE console.knowledge_bases FORCE ROW LEVEL SECURITY;
CREATE POLICY company_isolation ON console.knowledge_bases
  USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid);
CREATE POLICY platform_admin_read_all ON console.knowledge_bases
  FOR SELECT TO console_platform USING (true);

ALTER TABLE console.knowledge_bases OWNER TO db_owner;
GRANT SELECT, INSERT, UPDATE, DELETE ON console.knowledge_bases TO console_app, console_platform;
