-- Runs AFTER Phinx has created console.agent_versions. PRD-001 §4.4-B, §7.7, §12.
-- Playground resolves an agent version's spec through this function rather than a v1_*
-- view, same reasoning as console.resolve_connection (004_resolve_connection_function.sql):
-- a view here would evaluate RLS as the view owner, not the caller, silently bypassing
-- company isolation. SECURITY DEFINER with its own explicit company_id match sidesteps
-- that entirely.
--
-- Returns `spec`, not `compiled_definition` - Playground compiles live on every run
-- (PRD §4.4-B: a runtime token's agent_version_id is never required to be published,
-- matching RuntimeTokenController::issueRuntimeToken(), which only checks the version
-- exists in the caller's company) rather than reading the publish-time snapshot that
-- `compiled_definition` is (PRD §4.4-A: only Publish persists a compiled definition;
-- every Save only validates transiently).
--
-- A version belonging to another company and one that doesn't exist are indistinguishable
-- (both return zero rows) - matches the PRD's repeated "404/not found, never reveal it
-- belongs to someone else" pattern (§12).

CREATE OR REPLACE FUNCTION console.resolve_agent_version(p_agent_version_id uuid, p_company_id uuid)
RETURNS TABLE(id uuid, spec jsonb, spec_version varchar)
LANGUAGE sql
SECURITY DEFINER
SET search_path = console, pg_temp
AS $$
  SELECT av.id, av.spec, av.spec_version
  FROM console.agent_versions av
  WHERE av.id = p_agent_version_id AND av.company_id = p_company_id;
$$;

REVOKE ALL ON FUNCTION console.resolve_agent_version(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION console.resolve_agent_version(uuid, uuid) TO ai_app;
