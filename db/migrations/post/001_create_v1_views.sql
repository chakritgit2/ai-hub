-- Runs AFTER Phinx (console-api) and Alembic (ai/) have created their tables. PRD-001 §8.1, §8.2.
-- v1_* views are the versioned read surface Python (ai_app / gateway_app) uses against schema `console`.
-- A breaking change ships as a new v2_* view; these are never altered in place once other services read them.
--
-- `security_invoker = true` on every view below: without it, Postgres evaluates RLS
-- *through* a view as the view's owner (whoever ran this script — a superuser in local
-- dev), not the querying role, so every v1_* view would silently return every company's
-- rows to ai_app/gateway_app regardless of app.company_id — confirmed live while building
-- the agent compiler (see db/migrations/post/004_resolve_connection_function.sql's
-- comment, which routes around this same issue for a different table via a
-- SECURITY DEFINER function instead). With security_invoker, the *querying role's own*
-- grants and the normal company_isolation RLS policies apply, same as any direct table
-- query — which is why the column-level GRANTs below are now required: they didn't matter
-- before (the view owner's privileges were used instead), they do now.
-- Nothing in the app queries these views yet, so this is a direct fix with nothing to
-- migrate around.

CREATE OR REPLACE VIEW console.v1_companies WITH (security_invoker = true) AS
SELECT id, code, name, status, monthly_budget_usd
FROM console.companies;

CREATE OR REPLACE VIEW console.v1_published_agents WITH (security_invoker = true) AS
SELECT
  av.id AS agent_version_id,
  av.company_id,
  av.agent_id,
  a.name AS agent_name,
  av.version_no,
  av.compiled_definition,
  av.compiler_version,
  av.dynamiq_version
FROM console.agent_versions av
JOIN console.agents a ON a.id = av.agent_id
WHERE av.is_published = true;

CREATE OR REPLACE VIEW console.v1_deployments WITH (security_invoker = true) AS
SELECT
  id, company_id, slug, environment, agent_version_id,
  rate_limit_per_min, daily_token_limit, daily_cost_limit_usd,
  allowed_origins, guardrail_overrides, output_mode,
  allow_write_tools, conversation_ttl_days, config_version, enabled
FROM console.deployments;

-- The tables backing these do not exist yet in this MVP skeleton (Tools/Egress/Skills/Knowledge Bases
-- are phase 2). Stubbed as empty views so the name/grant exist end-to-end without lying about data.
-- TODO(phase 2): replace with a real view once console.tools / console.egress_allowlist /
-- console.skill_versions / console.knowledge_bases are migrated. No FROM clause -> no
-- underlying table to apply security_invoker/RLS to yet, but set it now anyway so these
-- don't need a second look when they get real tables.
CREATE OR REPLACE VIEW console.v1_tools WITH (security_invoker = true) AS
SELECT
  NULL::uuid AS id, NULL::uuid AS company_id, NULL::varchar AS kind,
  NULL::varchar AS access_level, NULL::varchar AS auth_mode, NULL::jsonb AS config
WHERE false;

CREATE OR REPLACE VIEW console.v1_egress_allowlist WITH (security_invoker = true) AS
SELECT
  NULL::uuid AS id, NULL::uuid AS company_id, NULL::varchar AS host_pattern,
  NULL::integer AS port, NULL::boolean AS allow_private_ip
WHERE false;

CREATE OR REPLACE VIEW console.v1_published_skills WITH (security_invoker = true) AS
SELECT
  NULL::uuid AS skill_version_id, NULL::uuid AS company_id, NULL::varchar AS name,
  NULL::varchar AS object_key, NULL::boolean AS has_scripts
WHERE false;

CREATE OR REPLACE VIEW console.v1_knowledge_bases WITH (security_invoker = true) AS
SELECT
  NULL::uuid AS id, NULL::uuid AS company_id, NULL::varchar AS name,
  NULL::varchar AS retrieval_mode, NULL::numeric AS alpha
WHERE false;

GRANT SELECT ON
  console.v1_companies,
  console.v1_published_agents,
  console.v1_deployments,
  console.v1_tools,
  console.v1_egress_allowlist,
  console.v1_published_skills,
  console.v1_knowledge_bases
TO ai_app, gateway_app;

-- Column-level grants on the tables actually backing the views above (security_invoker
-- means these are now checked instead of the view owner's blanket privileges) — scoped to
-- exactly the columns each view selects or filters/joins on, not a blanket table grant.
GRANT SELECT (id, code, name, status, monthly_budget_usd)
  ON console.companies TO ai_app, gateway_app;
GRANT SELECT (id, company_id, agent_id, version_no, compiled_definition, compiler_version, dynamiq_version, is_published)
  ON console.agent_versions TO ai_app, gateway_app;
GRANT SELECT (id, name)
  ON console.agents TO ai_app, gateway_app;
GRANT SELECT (
  id, company_id, slug, environment, agent_version_id,
  rate_limit_per_min, daily_token_limit, daily_cost_limit_usd,
  allowed_origins, guardrail_overrides, output_mode,
  allow_write_tools, conversation_ttl_days, config_version, enabled
) ON console.deployments TO ai_app, gateway_app;
