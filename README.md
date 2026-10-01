# Dynamiq AI Management Console

Skeleton implementation of PRD-001 (`docs/prd/prd-001-dynamiq-ai-console.en.md`, mockup at
`docs/prd/mockup-001-dynamiq-ai-console.md`). This is a **runnable scaffold**, not a finished
product — Phase 1 MVP alone is estimated at ~61.5 person-days across 3 developers; each service
here boots and exposes its Phase-1 routes/pages, with most handlers returning `501` until
implemented.

## Services

| Path | Stack | Role |
|------|-------|------|
| `console-api/` | Phalcon 5 / PHP 8.1 | Control plane: users, companies, permissions, all config CRUD |
| `ai/` | Python 3.12, FastAPI, `dynamiq==0.65.0` | Data plane: `ai-runtime` (Playground + internal API), `ai-gateway` (public API), `ai-worker` (ARQ jobs) |
| `web/` | SvelteKit (Svelte 5) | Console UI |

Shared: `contracts/openapi/` (source of truth for API shape — see its README for the
operationId-naming convention shared across all three services), `db/` (schemas, roles, RLS,
`v1_*` views — see its README for migration order), `deploy/` (placeholder Docker/K8s).

This project consumes the `dynamiq` Python library (the parent repo) as a normal pip/uv
dependency pinned to `0.65.0` — it does not modify the library.

## Local development

```sh
docker compose up -d          # Postgres 16+pgvector, Redis, MinIO

psql "$DATABASE_URL" -f db/migrations/pre/001_schemas_roles_extensions.sql

(cd console-api && composer install && vendor/bin/phinx migrate)
(cd ai && uv sync && uv run alembic upgrade head)

psql "$DATABASE_URL" -f db/migrations/post/001_create_v1_views.sql \
                      -f db/migrations/post/002_rls_policies.sql \
                      -f db/migrations/post/003_resolve_api_key_function.sql

(cd console-api && php -S localhost:8000 -t public public/index.php)  # console-api
(cd ai && uv run uvicorn app.main_runtime:app --port 8080)  # ai-runtime
(cd web && npm install && npm run dev)                    # web
```

See each service's own README for details.
