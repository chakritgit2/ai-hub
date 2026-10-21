-- Same reasoning as 005_console_app_cross_schema_grants.sql: logs.run_steps' own
-- company_isolation policy (ai/alembic/versions/0022) has no `TO` clause, so console_app
-- only needs the table-level SELECT grant to read it (console-api already has USAGE on
-- `logs` from the earlier grant).

GRANT SELECT ON logs.run_steps TO console_app;
