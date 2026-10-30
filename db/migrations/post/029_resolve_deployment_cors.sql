-- Lets an unauthenticated CORS preflight (OPTIONS) responder (ai/app/main_gateway.py,
-- PRD Goal #6 "full browser session-token experience") check a deployment's own
-- `allowed_origins` against the real browser `Origin` header purely from `slug` - a real
-- preflight request never carries the caller's eventual Authorization header (CORS spec:
-- custom headers are only declared via Access-Control-Request-Headers, never actually
-- sent on the preflight itself), so there is no company_id to scope a normal RLS session
-- with yet. Same SECURITY DEFINER, no-prior-auth shape console.resolve_api_key already
-- uses to bootstrap API-key auth itself (009_resolve_api_key_allowed_ips.sql) - `slug` is
-- already globally unique (deployments_slug_unique), so this is safe to expose with no
-- company scoping: the only thing it reveals to an anonymous caller is whether a slug
-- exists, whether it's enabled, and its own publicly-intended allowed_origins list (the
-- same information the real request's own CORS-enforced response would reveal anyway).
BEGIN;

CREATE OR REPLACE FUNCTION console.resolve_deployment_cors(p_slug varchar)
RETURNS TABLE(allowed_origins jsonb, enabled boolean)
LANGUAGE sql
SECURITY DEFINER
SET search_path = console, pg_temp
AS $$
  SELECT d.allowed_origins, d.enabled
  FROM console.deployments d
  WHERE d.slug = p_slug;
$$;

REVOKE ALL ON FUNCTION console.resolve_deployment_cors(varchar) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION console.resolve_deployment_cors(varchar) TO gateway_app;

COMMIT;
