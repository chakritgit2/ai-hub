-- console.skills/console.skill_versions didn't exist when 002_rls_policies.sql ran, so they
-- get the same bulk treatment that file gave agents+agent_versions together, in their own
-- file — same reasoning as 011_tools_rls_and_grants.sql did for tools (PRD §7.3).

ALTER TABLE console.skills ENABLE ROW LEVEL SECURITY;
ALTER TABLE console.skills FORCE ROW LEVEL SECURITY;
CREATE POLICY company_isolation ON console.skills
  USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid);
CREATE POLICY platform_admin_read_all ON console.skills
  FOR SELECT TO console_platform USING (true);

ALTER TABLE console.skill_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE console.skill_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY company_isolation ON console.skill_versions
  USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid);
CREATE POLICY platform_admin_read_all ON console.skill_versions
  FOR SELECT TO console_platform USING (true);

ALTER TABLE console.skills OWNER TO db_owner;
ALTER TABLE console.skill_versions OWNER TO db_owner;
GRANT SELECT, INSERT, UPDATE, DELETE ON console.skills, console.skill_versions
  TO console_app, console_platform;
