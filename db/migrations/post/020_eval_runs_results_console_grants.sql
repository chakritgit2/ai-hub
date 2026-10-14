-- Runs AFTER Alembic has created runtime.eval_runs/logs.eval_results (0015-0018).
-- PRD-001 §8.1: console_app reads runtime.eval_runs directly (listed explicitly in the
-- grants table) and logs.eval_results (same "read logs.*" line every other logs table
-- falls under) for EvalsController::listEvalRuns/getEvalRun/getEvalRunResults - same
-- reasoning as 005_console_app_cross_schema_grants.sql for logs.runs/runtime.conversations:
-- both tables' own company_isolation policies (ai/alembic/versions/0016, 0018) have no
-- `TO` clause, so they already apply to any role with a table-level SELECT grant. No
-- schema-level USAGE needed here - console_app already has USAGE ON SCHEMA runtime/logs
-- from 005.

GRANT SELECT ON runtime.eval_runs, logs.eval_results TO console_app, console_platform;
