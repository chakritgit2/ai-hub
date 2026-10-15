-- Runs AFTER Phinx has created console.agent_versions/console.workflow_versions.
-- PRD-001 §6.9, §7.7, §12.
--
-- Used by the Workflows compiler (ai/app/services/workflow_compiler.py) to resolve each
-- state's referenced agent_version_id. Same SECURITY DEFINER reasoning as
-- 007_resolve_agent_version_function.sql (a view here would evaluate RLS as the view
-- owner, not the caller, silently bypassing company isolation).
--
-- Unlike 007, this deliberately does NOT filter `WHERE is_published = true` - the Python
-- caller needs to see the row even when the referenced agent version exists but isn't
-- published yet, so it can tell that apart from "no agent version with this id for this
-- company" (two different, actionable compile errors). A row belonging to another
-- company and one that doesn't exist are still indistinguishable (both return zero rows)
-- - that boundary is a security property (PRD §12's "as if it didn't exist"); the
-- caller's own unpublished-vs-missing boundary is not.

CREATE OR REPLACE FUNCTION console.resolve_published_agent_version_for_workflow(
    p_agent_version_id uuid, p_company_id uuid
)
RETURNS TABLE(
    id uuid, agent_id uuid, version_no integer, is_published boolean,
    compiled_definition jsonb, compiler_version varchar, dynamiq_version varchar
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = console, pg_temp
AS $$
  SELECT av.id, av.agent_id, av.version_no, av.is_published,
         av.compiled_definition, av.compiler_version, av.dynamiq_version
  FROM console.agent_versions av
  WHERE av.id = p_agent_version_id AND av.company_id = p_company_id;
$$;

REVOKE ALL ON FUNCTION console.resolve_published_agent_version_for_workflow(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION console.resolve_published_agent_version_for_workflow(uuid, uuid) TO ai_app;
