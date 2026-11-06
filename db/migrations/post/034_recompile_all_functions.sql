-- Runs AFTER Phinx has created console.agent_versions. PRD §12: `recompile-all` re-runs
-- the compiler against every company's published agent versions after a Dynamiq/compiler
-- upgrade ("upgrade the compiler and run recompile-all on staging first - specs stay
-- unchanged"). That needs to read `spec` and write `compiled_definition`/`compiler_version`/
-- `dynamiq_version` across every company at once, but 001_create_v1_views.sql's grant on
-- console.agent_versions is SELECT-only and column-scoped (no `spec`, no UPDATE at all) -
-- ai_app was never meant to read/write this table directly. Same SECURITY DEFINER pattern
-- as 007/022's resolve_* functions, not a widened table grant.

CREATE OR REPLACE FUNCTION console.list_published_agent_versions_for_recompile()
RETURNS TABLE(id uuid, company_id uuid, spec jsonb)
LANGUAGE sql
SECURITY DEFINER
SET search_path = console, pg_temp
AS $$
  SELECT av.id, av.company_id, av.spec
  FROM console.agent_versions av
  WHERE av.is_published = true;
$$;

-- `AND is_published = true` closes the same race publishAgentVersion's own UPDATE already
-- guards against (AgentsController.php's "AND is_published = false" check) - a version
-- unpublished between the list read above and this write is left untouched rather than
-- resurrecting a stale compiled_definition under it. Returns no row when that race is hit,
-- which the caller treats as "skip, don't count as recompiled".
CREATE OR REPLACE FUNCTION console.update_recompiled_agent_version(
  p_id uuid, p_compiled_definition jsonb, p_compiler_version varchar, p_dynamiq_version varchar
)
RETURNS TABLE(id uuid)
LANGUAGE sql
SECURITY DEFINER
SET search_path = console, pg_temp
AS $$
  UPDATE console.agent_versions
  SET compiled_definition = p_compiled_definition,
      compiler_version = p_compiler_version,
      dynamiq_version = p_dynamiq_version,
      updated_at = now()
  WHERE id = p_id AND is_published = true
  RETURNING id;
$$;

-- ai_app only - recompile-all is an internal ops operation triggered by platform_admin via
-- ai-runtime (PRD §12); gateway_app's request path never needs it.
REVOKE ALL ON FUNCTION console.list_published_agent_versions_for_recompile() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION console.list_published_agent_versions_for_recompile() TO ai_app;
REVOKE ALL ON FUNCTION console.update_recompiled_agent_version(uuid, jsonb, varchar, varchar) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION console.update_recompiled_agent_version(uuid, jsonb, varchar, varchar) TO ai_app;
