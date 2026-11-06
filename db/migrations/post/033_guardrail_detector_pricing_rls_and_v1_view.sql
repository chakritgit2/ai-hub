-- Runs AFTER Phinx (console.guardrail_detector_pricing, just added). PRD §6.4-adjacent -
-- company-scoped per-request pricing for the 3 Azure-backed guardrail detectors, feeding
-- logs.runs.guardrail_cost_usd.

-- console-api's own read/write (console_app) - same company_isolation shape as every
-- other console.* table in 002_rls_policies.sql (predates this table).
ALTER TABLE console.guardrail_detector_pricing ENABLE ROW LEVEL SECURITY;
ALTER TABLE console.guardrail_detector_pricing FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS company_isolation ON console.guardrail_detector_pricing;
CREATE POLICY company_isolation ON console.guardrail_detector_pricing
  USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
  WITH CHECK (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid);
DROP POLICY IF EXISTS platform_admin_read_all ON console.guardrail_detector_pricing;
CREATE POLICY platform_admin_read_all ON console.guardrail_detector_pricing
  FOR SELECT TO console_platform USING (true);

ALTER TABLE console.guardrail_detector_pricing OWNER TO db_owner;
GRANT SELECT, INSERT, UPDATE, DELETE ON console.guardrail_detector_pricing TO console_app, console_platform;

-- ai-runtime/ai-gateway's read (ai_app, gateway_app) - via a versioned v1_* view, same
-- pattern 001_create_v1_views.sql established and its own comment explains in detail:
-- `security_invoker = true` is required for RLS to apply as the querying role (ai_app/
-- gateway_app), not this script's own superuser identity - without it, the view would
-- silently return every company's pricing rows regardless of app.company_id. Column-
-- level GRANT on the underlying table is required for the same reason (security_invoker
-- means the view no longer runs with the view owner's blanket privileges).
CREATE OR REPLACE VIEW console.v1_guardrail_detector_pricing WITH (security_invoker = true) AS
SELECT company_id, check_type, price_per_request_usd
FROM console.guardrail_detector_pricing;

GRANT SELECT ON console.v1_guardrail_detector_pricing TO ai_app, gateway_app;
GRANT SELECT (company_id, check_type, price_per_request_usd)
  ON console.guardrail_detector_pricing TO ai_app, gateway_app;
