-- Runs AFTER Phinx has created console.workflow_versions. PRD-001 §6.9 (phase 4, slice 2).
-- Playground resolves a workflow version's spec through this function rather than a
-- v1_* view, same reasoning as console.resolve_agent_version
-- (007_resolve_agent_version_function.sql) - a view here would evaluate RLS as the view
-- owner, not the caller, silently bypassing company isolation.
--
-- Returns `spec`, not `compiled_definition` - Playground compiles live on every run,
-- same as resolve_agent_version: a workflow version's own `is_published` status is not a
-- precondition for testing it (publishing only freezes a compiled_definition snapshot for
-- later export/review) - only each STATE's referenced agent must be published
-- (enforced inside workflow_compiler.py's own compile step, not here).
--
-- A version belonging to another company and one that doesn't exist are indistinguishable
-- (both return zero rows) - matches the PRD's repeated "404/not found, never reveal it
-- belongs to someone else" pattern (§12).

CREATE OR REPLACE FUNCTION console.resolve_workflow_version(p_workflow_version_id uuid, p_company_id uuid)
RETURNS TABLE(id uuid, spec jsonb, spec_version varchar)
LANGUAGE sql
SECURITY DEFINER
SET search_path = console, pg_temp
AS $$
  SELECT wv.id, wv.spec, wv.spec_version
  FROM console.workflow_versions wv
  WHERE wv.id = p_workflow_version_id AND wv.company_id = p_company_id;
$$;

-- Used by Playground (ai_app) only - no workflow deployment/gateway surface exists yet
-- (same reasoning 021_workflows_rls_and_grants.sql already used to skip a
-- v1_published_workflows view), so gateway_app is not granted here.
REVOKE ALL ON FUNCTION console.resolve_workflow_version(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION console.resolve_workflow_version(uuid, uuid) TO ai_app;
