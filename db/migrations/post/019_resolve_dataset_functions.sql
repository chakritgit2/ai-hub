-- Gives ai-runtime a read path to console.datasets/console.dataset_items (PRD §6.7), same
-- SECURITY DEFINER pattern as console.resolve_knowledge_base (016)/resolve_tool (012)/
-- resolve_connection (004) - company ownership checked inside the function rather than
-- relying on a v1_* view. console_app doesn't need this: it already reads its own
-- company's console.datasets/dataset_items rows directly via plain RLS-scoped SELECT
-- (017_datasets_rls_and_grants.sql).

CREATE OR REPLACE FUNCTION console.resolve_dataset(p_dataset_id uuid, p_company_id uuid)
RETURNS TABLE(id uuid, name varchar)
LANGUAGE sql
SECURITY DEFINER
SET search_path = console, pg_temp
AS $$
  SELECT d.id, d.name
  FROM console.datasets d
  WHERE d.id = p_dataset_id AND d.company_id = p_company_id;
$$;

CREATE OR REPLACE FUNCTION console.resolve_dataset_items(p_dataset_id uuid, p_company_id uuid)
RETURNS TABLE(id uuid, question text, expected_answer text, context text, tags jsonb)
LANGUAGE sql
SECURITY DEFINER
SET search_path = console, pg_temp
AS $$
  SELECT i.id, i.question, i.expected_answer, i.context, i.tags
  FROM console.dataset_items i
  WHERE i.dataset_id = p_dataset_id AND i.company_id = p_company_id;
$$;

-- Eval cost estimation/execution is an ai-runtime/ai-worker-only concern (PRD §7.3:
-- gateway_app is "as ai_app without KB/eval privileges") - EXECUTE deliberately goes to
-- ai_app only, not gateway_app, unlike resolve_knowledge_base/resolve_connection/resolve_tool
-- which the gateway's own run path also needs.
REVOKE ALL ON FUNCTION console.resolve_dataset(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION console.resolve_dataset(uuid, uuid) TO ai_app;
REVOKE ALL ON FUNCTION console.resolve_dataset_items(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION console.resolve_dataset_items(uuid, uuid) TO ai_app;
