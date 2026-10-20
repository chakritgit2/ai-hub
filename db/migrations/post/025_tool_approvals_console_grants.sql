-- Runs AFTER Alembic has created runtime.tool_approvals (0020-0021).
-- console_app only ever reads this table (PRD §8.1) - decisions are written via
-- ai-runtime's decideApproval, never directly by console-api.
GRANT SELECT ON runtime.tool_approvals TO console_app, console_platform;
