-- 018_model_pricing_grants.sql deliberately gave console_app SELECT-only on
-- console.model_pricing, because at the time rows were "maintained directly via
-- migration/SQL" with no admin UI. PricingController (console-api) is that admin UI now -
-- grant the write access its create/update/delete endpoints need. ai_app/console_platform
-- stay read-only; nothing in ai-runtime writes this table.
GRANT INSERT, UPDATE, DELETE ON console.model_pricing TO console_app;
