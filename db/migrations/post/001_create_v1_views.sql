-- Runs AFTER Phinx (console-api) and Alembic (ai/) have created their tables. PRD-001 §8.1, §8.2.
-- v1_* views are the versioned read surface Python (ai_app / gateway_app) uses against schema `console`.
-- A breaking change ships as a new v2_* view; these are never altered in place once other services read them.

CREATE OR REPLACE VIEW console.v1_companies AS
SELECT id, code, name, status, monthly_budget_usd
FROM console.companies;

CREATE OR REPLACE VIEW console.v1_published_agents AS
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

CREATE OR REPLACE VIEW console.v1_deployments AS
SELECT
  id, company_id, slug, environment, agent_version_id,
  rate_limit_per_min, daily_token_limit, daily_cost_limit_usd,
  allowed_origins, guardrail_overrides, output_mode,
  allow_write_tools, conversation_ttl_days, config_version, enabled
FROM console.deployments;

-- The tables backing these do not exist yet in this MVP skeleton (Tools/Egress/Skills/Knowledge Bases
-- are phase 2). Stubbed as empty views so the name/grant exist end-to-end without lying about data.
-- TODO(phase 2): replace with a real view once console.tools / console.egress_allowlist /
-- console.skill_versions / console.knowledge_bases are migrated.
CREATE OR REPLACE VIEW console.v1_tools AS
SELECT
  NULL::uuid AS id, NULL::uuid AS company_id, NULL::varchar AS kind,
  NULL::varchar AS access_level, NULL::varchar AS auth_mode, NULL::jsonb AS config
WHERE false;

CREATE OR REPLACE VIEW console.v1_egress_allowlist AS
SELECT
  NULL::uuid AS id, NULL::uuid AS company_id, NULL::varchar AS host_pattern,
  NULL::integer AS port, NULL::boolean AS allow_private_ip
WHERE false;

CREATE OR REPLACE VIEW console.v1_published_skills AS
SELECT
  NULL::uuid AS skill_version_id, NULL::uuid AS company_id, NULL::varchar AS name,
  NULL::varchar AS object_key, NULL::boolean AS has_scripts
WHERE false;

CREATE OR REPLACE VIEW console.v1_knowledge_bases AS
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
