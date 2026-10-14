-- Runs AFTER Phinx has created console.model_pricing (20261014000001). PRD-001 §8.2.
--
-- Deliberately NO `ENABLE ROW LEVEL SECURITY` here, unlike every other console.* table's
-- own post/ file (011_tools_rls_and_grants.sql, 015_kb_rls_and_grants.sql,
-- 017_datasets_rls_and_grants.sql, ...) - model_pricing has no `company_id` column at all,
-- because a model's price is the same for every company, so there is no per-row company
-- predicate RLS could even filter on. `db_owner` keeps ownership (write access), every
-- app role gets read-only SELECT - no role gets INSERT/UPDATE/DELETE, since pricing rows
-- are maintained directly via migration/SQL (see the creating migration's own comment),
-- not through any controller.
--
-- ai_app needs this (unlike most console.* tables, which it only reads through a
-- `v1_*` view/SECURITY DEFINER function, PRD §7.3) because `eval_runner.estimate_eval_cost`
-- runs entirely inside ai-runtime and looks pricing up directly - there's no per-company
-- RLS concern to route around with a resolver function here, so a plain grant is simpler
-- and correct.

ALTER TABLE console.model_pricing OWNER TO db_owner;
GRANT SELECT ON console.model_pricing TO console_app, console_platform, ai_app;
