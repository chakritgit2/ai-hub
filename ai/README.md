# ai/ — Dynamiq AI Management Console runtime

Python 3.12 / FastAPI / Dynamiq 0.65.0 skeleton for the `ai-runtime`, `ai-gateway`
and `ai-worker` services described in `dynamiq-console/docs/prd/prd-001-dynamiq-ai-console.en.md`.

This directory is self-contained: it consumes the `dynamiq` library as a pip
dependency (`dynamiq==0.65.0`) and never modifies the parent repository.

## Layout

- `app/main_runtime.py` — ai-runtime FastAPI app (port 8080): `/ai/v1/*` (Playground) and
  `/internal/v1/*` (console-api only, port 8081 in production topology; see PRD §9.2/9.3).
- `app/main_gateway.py` — ai-gateway FastAPI app: `/v1/deployments/*` + `/.well-known/jwks.json`.
- `app/worker.py` — ARQ worker settings (indexing, evals, cleanup jobs).
- `app/core/` — settings, DB engine, auth stubs, crypto stubs, log redaction, tracing stub.
- `app/services/` — business logic (`runtime.py` and `conversations.py` are fully wired to
  Dynamiq; everything else in this skeleton is still a stub).
- `app/integrations/` — Dynamiq adapters, skill registry, safe HTTP client, OKF parser, Thai tokenizer.
- `app/jobs/` — ARQ job functions.
- `alembic/` — async-capable Alembic migrations for `runtime.*` and `logs.*` tables.
- `tests/` — pytest suite (health checks + a real Dynamiq Agent smoke test).

## Running locally

```bash
# 1. Install dependencies (creates .venv; pulls dynamiq==0.65.0 and its deps, a few minutes)
uv sync

# 2. Copy env and fill in DATABASE_URL / REDIS_URL / OPENAI_API_KEY as needed
cp .env.example .env

# 3. Run migrations (requires a reachable Postgres; safe to skip for the boot check below)
uv run alembic upgrade head

# 4. Run the runtime API
uv run uvicorn app.main_runtime:app --port 8080

# In another shell: the gateway API
uv run uvicorn app.main_gateway:app --port 8090

# The worker
uv run arq app.worker.WorkerSettings
```

## Smoke test

```bash
curl localhost:8080/healthz
curl -X POST localhost:8080/ai/v1/playground/run \
  -H "Content-Type: application/json" \
  -d '{"input": "say hi in 3 words"}'
```

The playground run route (`app/services/runtime.py`) builds a real Dynamiq
`Agent` backed by `OpenAI` (connection + LLM) and calls `agent.run(...)`. It
requires `OPENAI_API_KEY` to be set; without it, the route returns a clear
502 with the underlying provider error rather than failing to import.

When the request also includes `X-Company-Id` (header) and `user.external_id`
(body), the run is wired to real conversation memory (`app/services/
conversations.py`, Dynamiq's PostgreSQL memory backend against
`runtime.agent_memory`) — pass the same `conversation_id` back on a later
call to continue that conversation. `X-Company-Id` is a temporary stand-in
for real auth (`app/core/auth.py` is still a stub); memory requires
`alembic upgrade head` to have been run (migrations `0004`-`0006` add the
RLS policy/grants, the `agent_memory` table, and the expiry-sweep function it
needs). Without both `X-Company-Id` and `user.external_id` the run proceeds
without memory and the response's `conversation_id` is `null`.

Everything else in `main_runtime.py`/`main_gateway.py` that isn't explicitly
called out as "REAL" in the PRD-driven build plan returns `501 Not
Implemented` — this is a bootable skeleton, not a feature-complete service.

## Tests

```bash
uv run pytest
```

Tests that need a live Postgres/Redis/OpenAI key are skipped automatically
when those aren't available in the environment.
