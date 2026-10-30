-- 029_resolve_deployment_cors.sql granted console.resolve_deployment_cors to gateway_app
-- only, breaking the convention every other SECURITY DEFINER resolve_* function in this
-- schema follows (resolve_api_key, resolve_workflow_identity, resolve_egress_allowlist,
-- resolve_tool, ...: all granted to both ai_app and gateway_app) - found because the
-- requires_postgres tests in test_gateway_cors_preflight.py fail with "permission denied
-- for function resolve_deployment_cors" against the shared local/test ai_app role.
BEGIN;

GRANT EXECUTE ON FUNCTION console.resolve_deployment_cors(varchar) TO ai_app;

COMMIT;
