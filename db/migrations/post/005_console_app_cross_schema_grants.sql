-- Runs AFTER both Phinx (console.* tables) and Alembic (runtime.*/logs.* tables) have
-- created their tables. PRD-001 §8.1: console-api's `console_app` role reads logs.*/
-- runtime.* directly for Runs/Dashboard - no view layer needed here (unlike the
-- opposite ai_app/gateway_app -> console.* direction, which needed `v1_*` views in
-- 001_create_v1_views.sql because superuser-owned views evaluate RLS as the owner).
-- logs.runs/logs.guardrail_events/runtime.conversations' own company_isolation policies
-- (ai/alembic/versions/0004, 0009, 0010) have no `TO` clause, so they already apply to
-- any role with a table-level SELECT grant - console_app just needed that grant, plus
-- schema-level USAGE on `runtime` (it already had USAGE on `logs` from an earlier grant).

GRANT USAGE ON SCHEMA runtime TO console_app;
GRANT SELECT ON logs.runs, logs.guardrail_events, runtime.conversations TO console_app;
