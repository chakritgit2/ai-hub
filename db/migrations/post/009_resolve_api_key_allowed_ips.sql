-- Adds k.allowed_ips to console.resolve_api_key's result (PRD §7.7's per-key IP
-- allowlist). The function joins console.api_keys as k already; this only exposes one
-- more of its own columns to the caller, who enforces the match against
-- request.client.host itself (SECURITY DEFINER functions don't see the caller's network
-- info) - matching semantics (company/scope/revoked/enabled) are unchanged.
--
-- CREATE OR REPLACE can't change a function's RETURNS TABLE column list, so this drops
-- and recreates it (same as 008 did implicitly via its own CREATE OR REPLACE, which only
-- worked because that one didn't change the signature - this one does). Applied with
-- `psql -f` outside any migration-runner transaction (db/README.md), so the DROP and
-- CREATE are wrapped in one explicit transaction here - otherwise a request racing this
-- migration could hit the gap between them and get a bare "function does not exist"
-- error instead of the gateway's normal 404 handling.
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
  WHERE k.key_hash = p_key_hash
    AND d.slug = p_slug
    AND k.revoked_at IS NULL
    AND d.enabled = true;
$$;

REVOKE ALL ON FUNCTION console.resolve_api_key(varchar, varchar) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION console.resolve_api_key(varchar, varchar) TO gateway_app, ai_app;

COMMIT;
