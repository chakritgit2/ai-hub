-- Runs BEFORE Phinx (console-api) and Alembic (ai/). PRD-001 §8.1.
-- Creates the three schemas, the pgvector extension, and placeholder DB roles.
-- Actual GRANTs are added in db/migrations/post/ once Phinx/Alembic have created their tables.

CREATE SCHEMA IF NOT EXISTS console;
CREATE SCHEMA IF NOT EXISTS runtime;
CREATE SCHEMA IF NOT EXISTS logs;

CREATE EXTENSION IF NOT EXISTS vector;

-- db_owner: owns every table, used only for migrations (Phinx + Alembic connect as this role).
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'db_owner') THEN
    CREATE ROLE db_owner LOGIN PASSWORD 'changeme_local_dev_only';
  END IF;
END $$;

-- console_app: Phalcon (console-api). Read/write `console`, read selected runtime/logs tables (PRD §8.1).
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'console_app') THEN
    CREATE ROLE console_app LOGIN PASSWORD 'changeme_local_dev_only';
  END IF;
END $$;

-- console_platform: Phalcon, platform_admin — console_app + cross-company read policies.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'console_platform') THEN
    CREATE ROLE console_platform LOGIN PASSWORD 'changeme_local_dev_only';
  END IF;
END $$;

-- ai_app: ai-runtime + ai-worker. Read/write `runtime` and `logs`, read console `v1_*` views.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ai_app') THEN
    CREATE ROLE ai_app LOGIN PASSWORD 'changeme_local_dev_only';
  END IF;
END $$;

-- gateway_app: ai-gateway. Same as ai_app without KB/eval privileges.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gateway_app') THEN
    CREATE ROLE gateway_app LOGIN PASSWORD 'changeme_local_dev_only';
  END IF;
END $$;

GRANT USAGE ON SCHEMA console TO console_app, console_platform, ai_app, gateway_app;
GRANT USAGE ON SCHEMA runtime TO ai_app, gateway_app;
GRANT USAGE ON SCHEMA logs TO console_app, console_platform, ai_app, gateway_app;

-- Schemas are created by whichever role runs this script (typically the postgres
-- superuser), so db_owner/ai_app need an explicit CREATE grant to run migrations —
-- USAGE alone only lets them SELECT/etc. on objects that already exist.
-- db_owner runs Phinx (schema `console`) and owns every table it creates there
-- (PRD §7.3/§8.1: app roles never own tables); ai_app runs Alembic directly against
-- `runtime`/`logs` and owns what it creates (no separate owner role on the Python side).
GRANT USAGE, CREATE ON SCHEMA console TO db_owner;
GRANT CREATE ON SCHEMA runtime, logs TO ai_app;

-- console-api's migrations install `pgcrypto` themselves (see the comment in
-- 20260925000001_create_companies_table.php) rather than having it created here
-- alongside `vector` — CREATE EXTENSION needs database-level CREATE, which a freshly
-- created role doesn't have by default.
GRANT CREATE ON DATABASE ai_console TO db_owner;

-- ai/'s first Alembic migration re-asserts `CREATE SCHEMA IF NOT EXISTS runtime`
-- (belt-and-suspenders) — Postgres checks database-level CREATE before it even gets
-- to evaluate IF NOT EXISTS, so ai_app needs this too even though the schema already
-- exists by the time Alembic runs.
GRANT CREATE ON DATABASE ai_console TO ai_app;
