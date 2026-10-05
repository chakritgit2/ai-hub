-- Gives ai-runtime a read path to console.tools (PRD §6.5), same SECURITY DEFINER pattern
-- as console.resolve_connection (004) — company_id match done inside the function rather
-- than relying on a v1_* view (see 004's comment for why views are avoided for anything
-- used in a real authz decision). console_app doesn't need this: it already reads its own
-- company's console.tools rows directly via plain RLS-scoped SELECT (011).

CREATE OR REPLACE FUNCTION console.resolve_tool(p_tool_id uuid, p_company_id uuid)
RETURNS TABLE(
  id uuid, name varchar, kind varchar, access_level varchar, auth_mode varchar,
  audience varchar, config jsonb, enabled boolean
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = console, pg_temp
AS $$
  SELECT t.id, t.name, t.kind, t.access_level, t.auth_mode, t.audience, t.config, t.enabled
  FROM console.tools t
  WHERE t.id = p_tool_id AND t.company_id = p_company_id;
$$;

REVOKE ALL ON FUNCTION console.resolve_tool(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION console.resolve_tool(uuid, uuid) TO ai_app;
