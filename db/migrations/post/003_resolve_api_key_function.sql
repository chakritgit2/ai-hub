-- Runs AFTER Phinx has created console.api_keys/console.deployments. PRD-001 §7.3, §7.7.
-- The gateway resolves the company via this SECURITY DEFINER function, which returns
-- company_id/deployment_id only when the key belongs to the deployment's company and the
-- deployment is in the key's scope (api_key_scopes — not yet migrated in this MVP skeleton,
-- so this stub checks company match only; TODO(phase 1 completion): add the scope join once
-- console.api_key_scopes exists).

CREATE OR REPLACE FUNCTION console.resolve_api_key(p_key_hash varchar, p_slug varchar)
RETURNS TABLE(company_id uuid, deployment_id uuid)
LANGUAGE sql
SECURITY DEFINER
SET search_path = console, pg_temp
AS $$
  SELECT d.company_id, d.id AS deployment_id
  FROM console.api_keys k
  JOIN console.deployments d ON d.company_id = k.company_id
  WHERE k.key_hash = p_key_hash
    AND d.slug = p_slug
    AND k.revoked_at IS NULL
    AND d.enabled = true;
$$;

REVOKE ALL ON FUNCTION console.resolve_api_key(varchar, varchar) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION console.resolve_api_key(varchar, varchar) TO gateway_app, ai_app;
