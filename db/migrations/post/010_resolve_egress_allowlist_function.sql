-- Gives ai-runtime/ai-gateway a read path to console.egress_allowlist (PRD §7.4), which
-- today only console_app/console_platform have a direct grant on (006). Same
-- SECURITY DEFINER pattern as console.resolve_connection (004) and console.resolve_api_key
-- (009) — company_id match done inside the function rather than relying on a v1_* view
-- (see 004's comment for why views are avoided for anything used in a real authz decision).
--
-- Scoped to the caller's own company_id only. egress_allowlist.company_id is nullable for a
-- future platform-wide row (PRD §8.1), but nothing creates one yet and the table's RLS
-- company_isolation policy (006) can't let a company-scoped role write one either — adding
-- `OR company_id IS NULL` here is a one-line follow-up once something can actually create
-- such a row, not before.

CREATE OR REPLACE FUNCTION console.resolve_egress_allowlist(p_company_id uuid)
RETURNS TABLE(host_pattern varchar, port integer, allow_private_ip boolean)
LANGUAGE sql
SECURITY DEFINER
SET search_path = console, pg_temp
AS $$
  SELECT e.host_pattern, e.port, e.allow_private_ip
  FROM console.egress_allowlist e
  WHERE e.company_id = p_company_id;
$$;

REVOKE ALL ON FUNCTION console.resolve_egress_allowlist(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION console.resolve_egress_allowlist(uuid) TO ai_app, gateway_app;
