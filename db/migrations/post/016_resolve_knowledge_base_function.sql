-- Gives ai-runtime a read path to console.knowledge_bases (PRD §6.6), same SECURITY
-- DEFINER pattern as console.resolve_tool (012)/console.resolve_connection (004) — company
-- ownership checked inside the function rather than relying on a v1_* view. console_app
-- doesn't need this: it already reads its own company's console.knowledge_bases rows
-- directly via plain RLS-scoped SELECT (015).

CREATE OR REPLACE FUNCTION console.resolve_knowledge_base(p_kb_id uuid, p_company_id uuid)
RETURNS TABLE(
  id uuid, name varchar, embedder_connection_id uuid, chunk_size int, chunk_overlap int,
  retrieval_mode varchar, alpha numeric, okf_field_map jsonb
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = console, pg_temp
AS $$
  SELECT kb.id, kb.name, kb.embedder_connection_id, kb.chunk_size, kb.chunk_overlap,
         kb.retrieval_mode, kb.alpha, kb.okf_field_map
  FROM console.knowledge_bases kb
  WHERE kb.id = p_kb_id AND kb.company_id = p_company_id;
$$;

-- Used by both the compiler (ai_app) and the gateway's run path (gateway_app) - same
-- reasoning as console.resolve_connection.
REVOKE ALL ON FUNCTION console.resolve_knowledge_base(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION console.resolve_knowledge_base(uuid, uuid) TO ai_app, gateway_app;
