-- Runs AFTER Phinx has created console.workflows/console.workflow_versions and added
-- console.deployments.workflow_version_id. PRD §6.3/§6.9 phase 4 workflow deployment
-- exposure.
--
-- The public gateway's GET /v1/deployments/{slug}/info needs *something* to show for a
-- workflow-backed deployment, mirroring what it already shows for an agent-backed one
-- (display_name/role/languages, read from the uncompiled AgentIdentitySpec - deliberately
-- never the compiled definition, which can embed connection/tool config). A workflow has
-- no identity-shaped spec field at all (WorkflowSpecDoc is states/edges/manager/max_loops
-- only) - the closest equivalent is simply the workflow's own `name`. This function
-- exposes only that one column, never `spec`/`compiled_definition` (which embed every
-- state's agent_compiled_definition, including resolved connection/tool config - far more
-- sensitive than an agent's own compiled_definition, which is why getDeploymentInfo reads
-- the agent's spec, not compiled_definition, in the first place).
--
-- A version belonging to another company and one that doesn't exist are indistinguishable
-- (both return zero rows) - same "404, never reveal it belongs to someone else" pattern
-- as console.resolve_agent_version (007_resolve_agent_version_function.sql) and
-- console.resolve_workflow_version (023_resolve_workflow_version_function.sql).

CREATE OR REPLACE FUNCTION console.resolve_workflow_identity(p_workflow_version_id uuid, p_company_id uuid)
RETURNS TABLE(name varchar)
LANGUAGE sql
SECURITY DEFINER
SET search_path = console, pg_temp
AS $$
  SELECT w.name
  FROM console.workflow_versions wv
  JOIN console.workflows w ON w.id = wv.workflow_id
  WHERE wv.id = p_workflow_version_id AND wv.company_id = p_company_id;
$$;

-- Same grant pair as console.resolve_agent_version (007_resolve_agent_version_function.sql)
-- and console.resolve_api_key: this ai/ codebase's gateway process connects to Postgres
-- as ai_app (one shared DATABASE_URL for both main_runtime.py and main_gateway.py; there
-- is no separate gateway_app-authenticated connection today) - confirmed live, a
-- gateway_app-only grant 403s getDeploymentInfo for every workflow-backed deployment.
-- gateway_app is granted too for parity with resolve_agent_version/resolve_api_key, in
-- case a future split ever gives the gateway its own DB role.
REVOKE ALL ON FUNCTION console.resolve_workflow_identity(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION console.resolve_workflow_identity(uuid, uuid) TO ai_app, gateway_app;
