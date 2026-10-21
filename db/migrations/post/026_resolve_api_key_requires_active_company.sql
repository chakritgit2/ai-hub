-- PRD §7.3: "Suspending a company makes its deployments return 403 immediately."
-- console.resolve_api_key (003 -> 008 -> 009) never checked company status, so a suspended
-- company's gateway API key kept resolving successfully. The primary fix is at the
-- application layer (ai-gateway's require_gateway_auth/require_gateway_api_key, which also
-- covers session tokens - this function can't, since a session-token call never invokes
-- it). This is DB-layer defense in depth for this one function specifically.
--
-- CREATE OR REPLACE can't change a function's RETURNS TABLE column list, but this one
-- doesn't change it - still DROP+CREATE wrapped in one transaction (matching 009) so a
-- request racing this migration can't hit a bare "function does not exist" error.
BEGIN;

DROP FUNCTION IF EXISTS console.resolve_api_key(varchar, varchar);

CREATE FUNCTION console.resolve_api_key(p_key_hash varchar, p_slug varchar)
RETURNS TABLE(company_id uuid, deployment_id uuid, allowed_ips jsonb)
LANGUAGE sql
SECURITY DEFINER
SET search_path = console, pg_temp
AS $$
  SELECT d.company_id, d.id AS deployment_id, k.allowed_ips
  FROM console.api_keys k
  JOIN console.deployments d ON d.company_id = k.company_id
  JOIN console.api_key_scopes s ON s.api_key_id = k.id AND s.deployment_id = d.id
  JOIN console.companies c ON c.id = d.company_id
  WHERE k.key_hash = p_key_hash
    AND d.slug = p_slug
    AND k.revoked_at IS NULL
    AND d.enabled = true
    AND c.status = 'active';
$$;

REVOKE ALL ON FUNCTION console.resolve_api_key(varchar, varchar) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION console.resolve_api_key(varchar, varchar) TO gateway_app, ai_app;

COMMIT;
