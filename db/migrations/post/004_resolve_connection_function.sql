-- Runs AFTER Phinx has created console.connections. PRD-001 §7.7, §12.
-- The agent compiler resolves a spec's model.connection_id through this function rather
-- than a v1_* view: every v1_* view in db/migrations/post/001_create_v1_views.sql is owned
-- by whoever runs this script (a superuser in local dev), and Postgres evaluates RLS
-- *through* a view as the view's owner, not the querying role, by default — so RLS on
-- console.connections would be silently bypassed for every caller via a view, regardless
-- of app.company_id (confirmed live: unset/wrong app.company_id still returned rows
-- through a plain SELECT ... FROM console.connections-based view). SECURITY DEFINER with
-- its own explicit company_id match sidesteps that entirely, exactly like
-- console.resolve_api_key already does for gateway API keys.
--
-- A connection belonging to another company and one that doesn't exist are
-- indistinguishable (both return zero rows) — matches the PRD's repeated "404/not found,
-- never reveal it belongs to someone else" pattern (§12).
--
-- TODO(follow-up, not this change): the same owner-evaluates-RLS issue affects every
-- v1_* view in 001_create_v1_views.sql. Nothing reads those for a real authorization
-- decision yet, so it's not fixed here — add `security_invoker = true` (+ matching
-- column-level grants) to all of them before anything starts relying on them for one.

CREATE OR REPLACE FUNCTION console.resolve_connection(p_connection_id uuid, p_company_id uuid)
RETURNS TABLE(id uuid, name varchar, type varchar, api_base varchar, max_concurrency integer)
LANGUAGE sql
SECURITY DEFINER
SET search_path = console, pg_temp
AS $$
  SELECT c.id, c.name, c.type, c.api_base, c.max_concurrency
  FROM console.connections c
  WHERE c.id = p_connection_id AND c.company_id = p_company_id;
$$;

-- Used by both the compiler (ai_app) and the gateway's run path (gateway_app), which
-- re-resolves a deployment's connection fresh on every run rather than trusting the
-- publish-time snapshot in compiled_definition.
REVOKE ALL ON FUNCTION console.resolve_connection(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION console.resolve_connection(uuid, uuid) TO ai_app, gateway_app;
