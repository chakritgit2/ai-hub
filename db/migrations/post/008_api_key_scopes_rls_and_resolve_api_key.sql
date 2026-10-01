-- Runs AFTER Phinx has created console.api_key_scopes (20260925000011). PRD-001 §7.7/§8.2.

-- Same company_isolation shape as every other console.* table in 002_rls_policies.sql
-- (that file predates this table, so it isn't there yet).
ALTER TABLE console.api_key_scopes ENABLE ROW LEVEL SECURITY;
ALTER TABLE console.api_key_scopes FORCE ROW LEVEL SECURITY;
CREATE POLICY company_isolation ON console.api_key_scopes
  USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
  WITH CHECK (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid);
CREATE POLICY platform_admin_read_all ON console.api_key_scopes
  FOR SELECT TO console_platform USING (true);

ALTER TABLE console.api_key_scopes OWNER TO db_owner;
GRANT SELECT, INSERT, UPDATE, DELETE ON console.api_key_scopes TO console_app, console_platform;

-- console.resolve_api_key (PRD §7.7: "resolves only when the key and deployment share a
-- company and the deployment is in scope") - 003_resolve_api_key_function.sql's own
-- comment already documented this exact gap as a TODO pending this table's existence.
--
-- EXECUTE stays limited to gateway_app/ai_app, same as 003 - this is a SECURITY DEFINER
-- function with no concept of "the caller's own company" (that's exactly what it
-- resolves), so granting it to console_app (the role behind authenticated web-user
-- requests) before ApiKeysController actually calls it and checks the returned
-- company_id against the caller's own session would make it a cross-company existence
-- oracle for no current benefit. Add the grant in the same change that wires up that
-- call and its company_id check, not ahead of it.
CREATE OR REPLACE FUNCTION console.resolve_api_key(p_key_hash varchar, p_slug varchar)
RETURNS TABLE(company_id uuid, deployment_id uuid)
LANGUAGE sql
SECURITY DEFINER
SET search_path = console, pg_temp
AS $$
  SELECT d.company_id, d.id AS deployment_id
  FROM console.api_keys k
  JOIN console.deployments d ON d.company_id = k.company_id
  JOIN console.api_key_scopes s ON s.api_key_id = k.id AND s.deployment_id = d.id
  WHERE k.key_hash = p_key_hash
    AND d.slug = p_slug
    AND k.revoked_at IS NULL
    AND d.enabled = true;
$$;

REVOKE ALL ON FUNCTION console.resolve_api_key(varchar, varchar) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION console.resolve_api_key(varchar, varchar) TO gateway_app, ai_app;
