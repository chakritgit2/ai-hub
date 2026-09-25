# PRD-001: Dynamiq AI Management Console

| Field  | Value |
|--------|-------|
| Status | Proposed (r10) |
| Author | Pannawat (design) + Claude (PRD authoring) |
| Date   | 2026-09-24 |
| Stack  | SvelteKit · Phalcon (PHP) · Python FastAPI + Dynamiq 0.65.0 · PostgreSQL 16 + pgvector · Redis · MinIO · kube-advws |

## Summary

This system is the **group-wide AI Gateway**: other applications (the existing Phalcon system, LINE Mini App, internal systems) call AI agents through a single API, and a management console configures agents, model selection, guardrails, logs and quality evaluation.

- **Phalcon** is the console backend (control plane): users, companies, permissions and all configuration.
- **Python (FastAPI + Dynamiq)** handles everything AI (data plane): running agents, SSE streaming, guardrails, memory, RAG, evaluation and logging.
- **A single PostgreSQL (HA)** is shared by both sides: database `ai_console` split into schemas `console` / `runtime` / `logs`, with privileges separated by DB roles.

## 1. Goals

1. **Central integration point (AI Gateway):** other applications call AI agents through one standard API, with a Python backend (FastAPI + Dynamiq) running and managing the agents.
2. **Model selection:** the LLM provider and model are chosen per agent from the web UI (e.g. OpenAI, Anthropic, Gemini), with an optional fallback model, without code changes.
3. **Logs & telemetry:** every request is recorded — input/output, step-by-step trace, tokens, latency, cost — and metrics are exported to Grafana.
4. **Guardrails:** inputs and outputs are checked (prompt injection, PII, unsafe content, output format) and usage is bounded (loops, tokens, quotas, rate limits, tool approval).
5. **AI evaluation:** answer quality is measured with datasets and metrics, and results are compared across agent versions before publishing.
6. **SSE streaming:** both the Playground and the external API stream answers token by token over Server-Sent Events.
7. **Multi-company:** multiple group companies share one system with **strict per-company separation** — API keys (both LLM keys and gateway keys), knowledge, skills, agents, logs, costs and users are never shared across companies (see 7.7); one user may belong to several companies.
8. **Knowledge & Skills:** knowledge in OKF (Markdown + frontmatter) and skills (`SKILL.md`) are managed from the web UI, per company, versioned and exportable.
9. **Agent Identity:** every agent has an identity that records **what the agent is for** — name, role, responsibilities, in-scope/out-of-scope, persona and language, human-handoff conditions and owner — editable from the web UI, versioned, and the single source of the agent's description for prompts, the catalog and multi-agent use (see 6.1a).

## 2. Non-goals

1. No visual drag-and-drop workflow builder — multi-agent/graph workflows are edited through a YAML/JSON editor only.
2. No SaaS-style multi-tenancy (self-signup, billing, per-tenant infrastructure/database) — only multi-company for group companies.
3. Non-admin roles cannot configure code-execution tools (Python tool, code interpreter).
4. No migration of data or logic from the existing Phalcon system, and the existing system's MariaDB is not used to store this system's data.

## 3. Background

Dynamiq is an Apache-2.0 Python framework for orchestrating LLMs, AI agents, RAG and multi-agent workflows. It supports roughly 30 LLM providers and ships memory backends, tools, guardrail detectors and evaluation metrics. It is a **library** — no UI, API server or user management. Today every agent change requires a Python script, and there is no central place for logs, cost or quality results.

Dynamiq capabilities used by this system:

| Capability | Dynamiq component |
|------------|-------------------|
| Agents/workflows defined as YAML/dict | `WorkflowYAMLLoader.parse()`, `Workflow.from_yaml_file_data()` |
| Streaming and tracing | `dynamiq.callbacks` |
| Memory on Postgres | `dynamiq.memory.backends.PostgreSQL` |
| Vector store on Postgres | `dynamiq.storages.vector.pgvector` (supports hybrid vector + keyword retrieval via `alpha`) |
| Markdown ingestion | `TextFileConverter`, `MarkdownHeaderSplitter` |
| Skills (`SKILL.md`) | `dynamiq.skills` (`BaseSkillRegistry`, `SkillsTool`) |
| Guardrail detectors / validators | `PromptInjectionDetector`, `PIIDetector`, `LlamaGuardDetector`, `RegexMatch`, `ValidJSON`, `ValidChoices` |
| Evaluation | `FaithfulnessEvaluator`, `ContextRecall/PrecisionEvaluator`, `AnswerCorrectnessEvaluator`, `FactualCorrectnessEvaluator`, BLEU, ROUGE, `LLMEvaluator`, `PythonEvaluator` |

Dynamiq **does not support MariaDB/MySQL** as a vector store or memory backend, so this system uses PostgreSQL.

## 4. Architecture

### 4.1 Overview

```
            [Internal network / VPN]                      [Public / Partner apps]
            SvelteKit (console-web)                       Phalcon system, LINE Mini App
              │ REST          │ SSE (Playground)                      │ API key / session token
              ▼               ▼                                       ▼
        console-api      ai-runtime  ◄──────── same image ────────►  ai-gateway
        (Phalcon)        (FastAPI)                                   (FastAPI, /v1/deployments)
              │ /internal/v1  │  └── enqueue ──► ai-worker (ARQ + Redis): indexing, eval
              └──────────────►│
              ▼               ▼
        ┌────────── PostgreSQL 16 + pgvector ──────────┐
        │ DB ai_console (CloudNativePG, primary+replica)│     MinIO (KB documents)
        │   schemas: console | runtime | logs           │
        └──────────────────────────────────────────────┘
              │ egress only via allowlist + NetworkPolicy
              ▼
        LLM providers · HTTP Tools → existing Phalcon system
```

### 4.2 Services

| Service | Language | Responsibility | Exposure |
|---------|----------|----------------|----------|
| `console-web` | SvelteKit (static) | The entire UI | internal / VPN |
| `console-api` | Phalcon (PHP) | Login/SSO, companies and permissions, all configuration CRUD, dashboard/reports, runtime token issuance | internal / VPN — `/admin/v1/*` |
| `ai-runtime` | Python FastAPI | Playground run/stream (port 8080) and the internal API for console-api — agent compilation, secrets, KB, eval (separate port 8081) | port 8080 `/ai/v1/*` via internal ingress; port 8081 `/internal/v1/*` in-cluster only (port-based NetworkPolicy) |
| `ai-gateway` | Python FastAPI (same image as ai-runtime) | Public API for external apps: run, stream, quota, CORS, JWKS | public — `/v1/deployments/*`, `/.well-known/jwks.json` |
| `ai-worker` | Python ARQ | Long-running jobs: document indexing, eval runs (low-priority queue), cleanup | no ingress |

### 4.3 Division of Responsibility

**Phalcon = control plane** (who, what is configured, who may do what) · **Python = data plane** (running AI and the data it produces)

- Configuration data (agent specs, tools, deployments, etc.) — written by Phalcon, read by Python through versioned views (`v1_*`).
- Agent specs are our own format — Python **compiles** them into Dynamiq definitions at publish time (see 6.1); Phalcon never writes Dynamiq YAML directly.
- Execution data (runs, conversations, memory, vectors, guardrail events, eval results) — written by Python, read by Phalcon for display.
- Secrets (LLM API keys) — stored and used only by Python; Phalcon cannot read them.
- Work requiring Dynamiq (config validation, connection tests, indexing, eval) — requested by Phalcon via `/internal/v1/*`.
- SSE never passes through PHP — browsers and external apps connect to Python directly.

**Alternative considered:** Python as the only backend. Rejected: the existing system and team are Phalcon-based and users/companies/permissions already live on the Phalcon side; keeping the control plane in Phalcon avoids syncing and lets the PHP team own the management UI backend.

**Alternative considered:** the existing system's MariaDB. Rejected: Dynamiq cannot use MariaDB as a vector store or memory backend, and MariaDB lacks the Row-Level Security used for company isolation.

### 4.4 Request Flows

**A. Edit and publish an agent**

1. A user edits an agent → `console-api` saves the **agent spec** (draft) in `console.agent_versions.spec`.
2. Save → `console-api` calls `POST /internal/v1/agents/compile` — `ai-runtime` checks the spec, checks node types against the role-based allowlist, compiles to a Dynamiq definition and validates it with Dynamiq; returns error paths or the compiled definition.
3. Publish → compile again with the current compiler; on success `console-api` stores `compiled_definition`, `compiler_version`, `dynamiq_version`, sets `is_published = true`, bumps the affected deployments' `config_version` and issues `NOTIFY agent_published`.
4. `ai-runtime` / `ai-gateway` evict cache on notification; additionally every cache hit compares `config_version` with the DB (lightweight query, result cached 5 s) — a lost notification cannot leave stale config for long.

**B. Playground (SSE)**

1. The web app requests a runtime token from `console-api` (`POST /admin/v1/runtime-token`) — a 5-minute JWT carrying `company_id`, `user_id`, `role`, `agent_version_id`.
2. The web app opens SSE to `ai-runtime` `POST /ai/v1/playground/stream` with the token.
3. `ai-runtime` verifies the token → runs the agent → streams events → writes logs.

**C. External app via the gateway**

1. Server-to-server: the app calls `POST /v1/deployments/{slug}/run` or `/stream` with an API key.
2. From an end-user browser: the app's server obtains a session token (`POST /v1/deployments/{slug}/session` with the API key) and the browser opens SSE directly.
3. `ai-gateway` checks key/token, quota, CORS → input guardrails → runs the agent → output guardrails → streams → writes logs.

## 5. Tech Stack

| Layer | Technology |
|-------|------------|
| Frontend | SvelteKit (Svelte 5, `adapter-static`), TypeScript, Tailwind + shadcn-svelte, Monaco Editor, TanStack Table/Query, LayerChart |
| Console backend | Phalcon 5 + PHP 8.x (matching the team's current version), PDO PostgreSQL, Phinx (migrations for tables in schema `console`) |
| AI backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async) + Alembic (schemas `runtime` and `logs`), Dynamiq `==0.65.0` |
| Worker | ARQ + Redis 7 (Sentinel, 3 nodes) |
| Database | PostgreSQL 16 + pgvector (single engine) on CloudNativePG: primary + replica with automatic failover; PgBouncer in transaction pooling mode |
| Object storage | MinIO (S3 API) |
| API contract | OpenAPI 3 (three files: console-api, ai-internal, ai-public) as the source of truth |
| Deploy | Docker (`--platform linux/amd64`) on kube-advws |

## 6. Features

### 6.1 Agents & Model Selection

- **Agent spec:** agents are stored in our own spec format (JSON, with `spec_version`) in `console.agent_versions.spec` — identity (6.1a), model (connection_id, model, temperature, max_tokens, fallback), tools, knowledge, skills, memory, guardrails, limits. Specs contain no Dynamiq class paths.
- **Compiler (Python):** translates spec → Dynamiq definition on save/publish and stores the result in `compiled_definition` with `compiler_version`; the runtime always executes `compiled_definition`. Upgrading Dynamiq means upgrading the compiler and recompiling every published version — specs stay unchanged.
- **Node-type allowlist:** the compiler emits only node/connection types allowed for the publisher's role — code execution (Python tool, E2B/Daytona, file tools) is `admin` only; any type outside the allowlist fails publishing.
- **Connections:** referenced by `connection_id` only — inline connections are forbidden; every connection's `api_base`/endpoint must pass the egress allowlist (7.4).
- Editor tabs: **Identity**, **Model**, **Tools**, **Knowledge**, **Skills**, **Memory**, **Guardrails**, **Advanced** — Advanced shows the `compiled_definition` read-only to every role; editing the spec as raw JSON is `admin` only (still through the compiler and allowlist).
- Versioning: drafts are freely editable; Publish makes a version immutable; rollback repoints a deployment to an older version.
- Provider errors (429/5xx): two retries with backoff, then the fallback model if configured.

### 6.1a Agent Identity

Identity **records what the agent is for** — it is part of the agent spec, versioned with the agent, and edited on the editor's **Identity** tab (form fields + Markdown for detail).

| Field | Meaning | Example |
|-------|---------|---------|
| `name` / `display_name` | Agent name seen by users and other systems | `vending-support` / "Vending Helper" |
| `role` | One-sentence main role | Answers customer questions about using machines and refunds |
| `responsibilities` | Duties the agent performs | Check order status, explain refund steps, recommend products |
| `in_scope` / `out_of_scope` | What it does and does not do | No topics outside the vending business; never grants discounts |
| `persona` | Personality and tone | Polite, concise |
| `languages` | Response languages | th (primary), en |
| `handoff` | When and where to hand off to a human | Refund above 500 THB → call center |
| `instructions` | Additional detail (Markdown) | Procedures, sample replies |
| `owner` / `tags` | Responsible owner and categories | CS team / support, vending |

**Where identity is used**

- **Prompt:** the compiler assembles identity into the Dynamiq Agent `role`/system prompt from a shared template — including instructions to politely decline anything in `out_of_scope` and to follow `handoff`.
- **Catalog:** the Agents page lists the company's agents with `display_name`, `role`, `responsibilities`, `owner` — the team can see which agents exist and what each does.
- **Multi-agent / agent-as-tool (phase 4):** `role` + `responsibilities` are the description an orchestrator uses to pick the right agent.
- **Gateway:** `GET /v1/deployments/{slug}/info` returns `display_name`, `role`, `languages` for calling apps to display (never `instructions`).
- **Trace and logs:** every run records `agent_name` and the identity version.
- **Evaluation (phase 3):** `in_scope` / `out_of_scope` seed test cases checking that the agent does its job and declines out-of-scope requests correctly.

### 6.2 Runtime, Streaming & Conversations

- SSE events: `token`, `step` (thought / tool_call / tool_result), `guardrail`, `final`, `error`, `usage`; a heartbeat every 15 s keeps proxies from closing idle connections.
- Per-run limits: `max_loops` (default 8, max 20), `max_tokens`, 120 s timeout, cancellable.
- Agents run through Dynamiq's `run_async` (sync nodes are offloaded to an executor by Dynamiq) — sync execution never blocks the event loop; concurrent runs are capped per pod (`MAX_CONCURRENT_RUNS`, default 16) with 429 + `Retry-After` beyond it.
- Graceful shutdown: on SIGTERM stop accepting work and wait up to 150 s for in-flight runs; unfinished runs are recorded as `interrupted`.
- **Conversations:** each conversation has a `conversation_id` (per deployment + `external_user_id`); if omitted a new one is created and returned in the `final` event; expiry per `conversation_ttl_days` (default 30).
- **Memory:** Dynamiq's PostgreSQL memory backend with an explicit `table_name = runtime.agent_memory` (Dynamiq's default `conversations` collides with our table); that table has no `company_id`, so RLS cannot apply — isolation relies on `user_id = "{company_id}:{external_user_id}"` and `session_id = conversation_id`, always set by the runtime and never taken from the request.

### 6.3 Deployments & Gateway

- A deployment is an endpoint bound to a published agent version; `slug` is globally unique in the form `<company_code>-<name>`.
- **Gateway API keys:** owned by a company — company admins create them on the **API Keys** page and scope each key to specific deployments of their own company; prefix `ak_{company_code}_`, stored hashed and shown once, server-to-server only, optional `allowed_ips` and per-key limits; 5-minute session tokens serve browsers (see 7.7).
- **Per-deployment limits:** `rate_limit_per_min`, `daily_token_limit`, `daily_cost_limit_usd` counted in Redis, reset at midnight Asia/Bangkok. Before a run the gateway **reserves** quota for `max_tokens` × model price and reconciles to actual usage on completion (preventing concurrent requests from overshooting); exceeding returns `429 quota_exceeded` with an alert at 80%.
- **If Redis is down:** rate limiting fails open (temporary in-pod limiter), but quotas **fail closed** for deployments with a `daily_cost_limit_usd`.
- CORS: only the deployment's `allowed_origins`, never `*`.
- Configurable per deployment: guardrail overrides, `output_mode` (stream/buffered), whether `write` tools are allowed, conversation TTL.
- The gateway caches published configuration in memory (checked by `config_version` per 4.4), so it tolerates brief DB failovers.
- `staging` / `production` environments + Promote, and agent spec YAML export/import (phase 3).

### 6.4 Guardrails

- Policies are defined per agent version and can be tightened per deployment.
- **Input checks:** `PromptInjectionDetector`, `PIIDetector` (mask/block), `LlamaGuardDetector`, maximum length, regex blocklist.
- **Output checks:** `LlamaGuardDetector`, `PIIDetector`, `RegexMatch` blocklist, `ValidJSON` / `ValidChoices`.
- Action per check: `block` (return a fallback message), `mask`, `flag` (pass through but record).
- **With SSE — tokens already sent cannot be recalled:**
    - Every chunk passes **fast checks** (Thai/international regex PII: phone numbers, national IDs, emails, account numbers; blocklist) *before* sending — masking happens inline with little added latency.
    - **Model checks** (LlamaGuard, model-based PII) run in windows (~200 tokens and at completion); a mid-stream block emits `guardrail` + `final` and closes the stream.
    - Deployments with a blocking output model check default to `output_mode = buffered` (full check before sending); switching to stream requires explicitly accepting the risk.
- `on_detector_error`: `block` (gateway default) or `flag` (Playground default).
- **Cost:** tokens/cost/latency of model-based detectors are counted in the run (as a separate `guardrail` line) and included in quotas and eval cost estimates.
- Every triggered check is written to `logs.guardrail_events` and shown in the trace.

### 6.5 Tools (Phase 2)

- HTTP Tool: URL, method, headers, JSON Schema input, description, `access_level` (`read` / `write`) and `auth_mode`:
    - `service` — a static service token, for data not tied to a user.
    - `delegated` — carries the end user's identity (see 7.5) — default for tools calling the Phalcon system.
- `write` tools require human approval (`approval_required` event, 5-minute window, otherwise rejected) and must be allowed by the deployment.
- Built-in tools (Tavily/Exa) can be toggled; all outbound calls go through `SafeHttpClient` (see 7.4).

### 6.6 Knowledge Bases — OKF (Phase 2)

**Canonical format:** all knowledge is stored as **OKF (Open Knowledge Format)** — one Markdown file per topic with YAML frontmatter metadata. The fields below are a **draft** pending the actual spec (Open Question 10); the frontmatter reader maps fields from configuration so they can change without code changes.

```markdown
---
id: refund-policy            # unique within the KB (missing → file path)
title: Vending refund policy # missing → first H1
tags: [refund, vending]
lang: th
version: 3
updated_at: 2026-09-01
source: Customer Service
---
# Refund policy
## Product not dispensed
...
```

- **Import:** upload single `.md` files or a `.zip` folder (folder path stored as `category`); PDF/DOCX/TXT are converted to Markdown first and stored as OKF too — everything in a KB shares one format.
- **Per company:** OKF files are stored in MinIO under `kb/{company_id}/{kb_id}/...`, vector tables are per KB of the company, and embeddings use only the owning company's connection; agents can attach only their own company's KBs (see 7.7).
- **Indexing:** frontmatter → metadata (filterable, e.g. `tags`, `lang`, `category`); body split with `MarkdownHeaderSplitter` on `#`/`##`/`###` (chunks keep their heading path), re-split recursively above ~800 tokens; embeddings → pgvector — one vector table per KB named `runtime.kbv_{company_id}_{kb_id}` (Dynamiq creates these without `company_id`), and the retriever resolves the table only from the KB owned by the company in context, never from the spec.
- **Editing in the UI:** a Markdown editor on the KB page; saving re-indexes only files whose `content_hash` changed.
- **Export:** download a KB as a `.zip` of `.md` files (for Git or other systems).
- **Retrieval:** per KB, `vector` or `hybrid` (pgvector vector + keyword, weighted by `alpha`, default 0.6).
- **Thai:** Postgres full-text cannot segment Thai (no spaces between words), so indexing stores a pre-tokenized `search_text` field (PyThaiNLP) and the keyword side uses the `simple` text search configuration.
- File status `queued → processing → ready | failed`; search can be tested with scores from the UI.
- **Knowledge graph** (Dynamiq `knowledge_graphs`) is an option in phase 4 — requires an additional graph database.

### 6.6a Skills (Phase 2)

- A skill is a set of instructions in `SKILL.md` (+ supporting files) in the `dynamiq.skills` format; agents load skills on demand via `SkillsTool` (`list` → `get`) instead of carrying everything in the prompt.
- Managed on the **Skills** page per company: create/edit with a Markdown editor or upload a `.zip`, versioned and published; attached to agents in the editor's **Skills** tab.
- The runtime uses our own `ConsoleSkillRegistry` (subclass of `BaseSkillRegistry`) reading published skill versions from Postgres/MinIO with caching — Dynamiq's cloud registry is not used.
- `SKILL.md` maximum 100 KB.
- **Skills with scripts** count as code execution — `admin` only and sandboxed (E2B/Daytona) — deferred to phase 3.

### 6.7 Evaluation (Phase 3)

- Datasets: upload CSV/JSON (`question`, `expected_answer`, `context?`, `tags?`) or add items from Playground/Runs.
- Eval Run: choose agent version + dataset + metrics + judge model → **cost estimate** shown before running → `ai-worker` processes items on a low-priority queue (concurrency 4), respecting per-connection concurrency limits so evals do not compete with production for provider rate limits.
- Metrics: Faithfulness, Context Recall/Precision, Answer/Factual Correctness, BLEU/ROUGE/string match (no LLM calls), `LLMEvaluator` (custom rubric), `PythonEvaluator` (admin only).
- Results: mean score per metric, per-item results with judge reasoning, Compare two runs, Publish gate (e.g. faithfulness ≥ 0.8).

### 6.8 Logs, Telemetry & Dashboard

- Every run is written to `runs` + `run_steps`: input/output, full trace, tokens in/out, latency, cost (from `model_pricing`), `trace_id`, company, source (`playground` / `api` / `eval`).
- `/metrics` (Prometheus): runs_total, run_latency, tokens_total, cost_usd_total, guardrail_triggers_total, quota_usage_ratio, queue_depth → Grafana.
- OpenTelemetry: `console-api` propagates `traceparent` to `ai-runtime` and on to HTTP Tools so traces span systems; Grafana Tempo in phase 3.
- Dashboard (Phalcon reads schema `logs`): runs today, tokens/cost this month, error rate, guardrail triggers, daily charts — per company, with an all-companies view for `platform_admin`.

### 6.9 Web Pages

| Menu | Key features | Phase |
|------|--------------|-------|
| Dashboard | Runs, tokens/cost, errors, guardrail triggers | 1 |
| Agents | Company catalog (name, role, responsibilities, owner), tabbed editor starting with Identity, test-chat panel, Versions (diff/publish/rollback) | 1 |
| Playground | Streaming chat, Trace panel, tokens/latency/cost, "Add as test case" | 1 |
| Connections | The company's credentials (masked), Test connection | 1 |
| Runs / Logs | Filters, trace detail, conversation view, guardrail events | 1 |
| Deployments | Slug, limits, CORS, guardrail overrides, cURL/PHP/Node samples | 1 |
| API Keys | The company's gateway API keys: create/revoke, per-deployment scope, `allowed_ips`, per-key limits, last used | 1 |
| Settings | Company users, egress allowlist; **Companies** (platform_admin) | 1 |
| Tools | HTTP/built-in tools, `auth_mode`, test call, approval queue for `write` tools | 2 |
| Knowledge Bases | Import `.md`/`.zip` (OKF), Markdown editor, indexing status, search test (vector/hybrid), export | 2 |
| Skills | Create/edit `SKILL.md`, upload `.zip`, versions, publish | 2 |
| Evaluation | Datasets, Eval Runs, Compare | 3 |
| Workflows | Multi-agent/graph via YAML editor | 4 |

## 7. Security & Multi-company

### 7.1 Authentication

- **Console users:** SSO through the existing Phalcon system (RS256 JWT + JWKS) with claims `sub`, `email`, `companies: [{ref, role}]`; `console-api` syncs `companies` and `company_members` on each login.
- **Web → ai-runtime (Playground):** runtime token issued by `console-api` (5-minute JWT).
- **console-api → ai-runtime (`/internal/v1/*`):** a 60-second JWT signed by `console-api` (`aud=ai-internal`) + `X-Company-Id` + `traceparent` — no static token; reachable only from `console-api` pods (NetworkPolicy).
- **External apps → ai-gateway:** the company's gateway API key (scoped per deployment) or a session token.

### 7.2 Roles

- `platform_admin` — ADVWS staff: create/suspend companies, model pricing, platform egress allowlist, cross-company **aggregate figures** (runs, tokens, cost) — but never company secrets, knowledge or conversation content.
- Per-company roles — `admin` (everything in the company, including Connections and code tools), `developer` (Agents, HTTP Tools, KB, Eval, Playground), `viewer` (Dashboard, Runs, Eval — read only).
- A user may belong to several companies with different roles; the UI has a company switcher and every `/admin/v1/*` request carries `X-Company-Id`, checked against membership each time.

### 7.3 Company Isolation

- Every business table carries `company_id`; unique constraints are company-scoped, e.g. `unique(company_id, name)`.
- **Layer 1 (application):** repositories on both the Phalcon and Python sides inject `company_id` from context; no endpoint accepts `company_id` in the body.
- **Layer 2 (database) — PostgreSQL RLS:**
    - `ENABLE` + `FORCE ROW LEVEL SECURITY` on every table with `company_id`; app roles **never own** tables (the owner is `db_owner`, used only for migrations).
    - The company is set with `SET LOCAL app.company_id` inside the transaction only (cleared at commit) — required with PgBouncer transaction pooling and PHP-FPM; session-level `SET` is forbidden.
    - Policies use `current_setting('app.company_id', true)` — when unset, no rows are visible (fail-closed).
    - The gateway resolves the company via the `SECURITY DEFINER` function `console.resolve_api_key(key_hash, slug)`, which returns `company_id` and `deployment_id` only when the key belongs to the deployment's company and has it in scope — then issues `SET LOCAL`.
    - `platform_admin` uses a separate `console_platform` role with explicit cross-company read policies, not `BYPASSRLS`.
    - Dynamiq's memory and vector tables have no `company_id` — isolated by namespace/table naming per 6.2 and 6.6.
- Gateway: a request's company always comes from its API key/session token.
- LLM keys belong to a single company — there are no cross-company shared connections (see 7.7); a per-connection `max_concurrency` (Redis semaphore) prevents one agent or eval from exhausting the provider's rate limits.
- Suspending a company (`status = suspended`) makes its deployments return 403 immediately.

### 7.4 Secrets, Egress & Tools

- **Secrets:** the Connection form sends values through `console-api` to `PUT /internal/v1/connections/{id}/secret` without persisting on the PHP side; `ai-runtime` envelope-encrypts them with the **company's key** (see 7.7) into `runtime.connection_secrets`; APIs return masks only; logs redact `Authorization` and fields matching `api_key|token|secret|password`.
- **Egress / SSRF:** all outbound HTTP goes through `SafeHttpClient` — host checked against `egress_allowlist`, internal IPs blocked (10/8, 172.16/12, 192.168/16, 127/8, 169.254/16, ::1, fc00::/7) unless explicitly allowed, no redirects outside the allowlist, 10 s timeout, ≤ 1 MB responses; NetworkPolicy permits egress only to Postgres, Redis, MinIO, DNS, LLM providers (via egress proxy) and the Phalcon system.
- **LLM/connection endpoints:** every connection's `api_base`/endpoint (including LLM providers) must pass the egress allowlist on save and at run time — connections cannot become an SSRF channel.
- **Prompt injection via data:** KB content and tool results are wrapped in delimiters and marked as data, not instructions; an agent can only call tools bound to its version.

### 7.5 End-user Identity

Agents act **on behalf of the asking user**, not the system — preventing an agent from fetching another user's data through a tool.

- **Identity sources:**
    - Server-to-server (API key): the calling app sends `user: {external_id, claims}` in the request — trusted because it comes from the app's server.
    - Browser (session token): the app's server embeds `external_user_id` and `claims` when issuing the session token — the browser cannot forge it, and the gateway **ignores** identity in the body when a session token is used.
    - Playground: the logged-in console user (`sub` from the runtime token), marked `playground=true`.
- **Propagation to tools (`auth_mode: delegated`):** before each tool call `ai-runtime` mints a 60-second JWT (RS256, `aud` = tool host) in the `Authorization` header with claims `company_id`, `sub` (external_user_id), `claims`, `act` (the agent/deployment acting), `run_id`.
- Target systems verify it against the JWKS that `ai-gateway` publishes at `GET /.well-known/jwks.json` and authorize by `sub` — e.g. the Phalcon system returns only that user's orders.
- Every tool call records `sub` and `act` in the trace for audit on both sides.
- Memory and conversations are already isolated per `external_user_id` (6.2).

### 7.6 Keys & Tokens

| Token | Issuer (holds private key) | Verifier | Lifetime | Verified via |
|-------|----------------------------|----------|----------|--------------|
| SSO JWT | Existing Phalcon system | console-api | per existing system | Existing system's JWKS |
| Runtime token (Playground) | console-api | ai-runtime | 5 min | console-api JWKS (`/admin/.well-known/jwks.json`, internal) |
| Internal call token | console-api | ai-runtime (port 8081) | 60 s | console-api JWKS, `aud=ai-internal` |
| Session token (browser) | ai-gateway | ai-gateway | 5 min | gateway key |
| Delegated identity token | ai-runtime / ai-gateway | Tool target system | 60 s | gateway JWKS (`/.well-known/jwks.json`) |
| Gateway API key (company-owned) | ai-gateway (generated) | ai-gateway | until revoked | hash in DB + scopes |

- Asymmetric keys (ES256/RS256) everywhere, with `kid`; private keys live in per-issuer K8s secrets.
- Rotation every 90 days: publish the new key in JWKS → start signing with it → remove the old key after the longest token lifetime.

### 7.7 Per-company API Keys and Knowledge

**LLM / connection keys**

- Every connection has a `company_id` and can be used only by that company's agents, KBs and evals — there are no shared connections, and `platform_admin` cannot create connections on a company's behalf.
- **Per-company envelope encryption:** each company has its own data key (DEK) in `runtime.company_keys`, itself encrypted with `CONSOLE_MASTER_KEY`; connection secrets are encrypted with the owning company's DEK — decrypted material of one company is useless for another, and a company can be erased by destroying its DEK (crypto-shredding).
- Provider cost and rate limits bind directly to the company's own key — no company can consume another's quota.

**Gateway API keys**

- Keys belong to a company, are managed by that company's `admin`, and can be scoped only to that company's deployments (`api_key_scopes`).
- The `ak_{company_code}_` prefix identifies the owner in logs or when a key leaks.
- `resolve_api_key(key_hash, slug)` resolves only when the key and deployment share a company and the deployment is in scope — otherwise 404, as if the deployment did not exist (revealing nothing about other companies).
- Usage, quotas and logs are tracked per key and per company.

**Knowledge & Skills**

- KBs, OKF files, vector tables, skills and their files belong to one company — nothing is shared or searchable across companies.
- MinIO: one bucket with per-company prefixes `kb/{company_id}/...` and `skills/{company_id}/...`; only `ai-runtime`/`ai-worker` access MinIO, always building paths from `company_id` in context (never from the request).
- The compiler verifies that every connection, KB, skill and tool referenced by an agent spec belongs to the agent's company — cross-company references fail compilation.
- Embedding and retrieval use the KB owner's connection; KB export is limited to that company's admins/developers.
- When companies need the same content (e.g. a group-wide machine manual), it is exported and imported into each company — separate, unlinked copies.

## 8. Data Model (PostgreSQL)

### 8.1 Layout and Privileges

```
PostgreSQL 16 + pgvector (CloudNativePG: primary + replica)
└── DB ai_console
    ├── schema console   tables: Phalcon (Phinx)   · v1_* views read by Python
    ├── schema runtime   tables: Python (Alembic)  · console reads selected tables, never secrets
    └── schema logs      tables: Python (Alembic)  · monthly partitions · console read-only
```

Logs share the database (separate schema) so the dashboard can join `console.agents`/`companies` directly; if logs grow large, old partitions move to a separate tablespace or ClickHouse.

**Shared-structure owner:** roles, grants, `v1_*` views, RLS policies and `SECURITY DEFINER` functions live in a repo-level `db/` (one migration set, run before Phinx/Alembic) — breaking view changes ship as new `v2_*` views; CI contract tests log in as each role and verify the required columns are selectable and other companies' rows are invisible.

| DB role | Privileges |
|---------|------------|
| `db_owner` | Owns every table — migrations only |
| `console_app` (Phalcon) | Read/write tables in `console`; read `runtime.conversations`, `runtime.kb_documents`, `runtime.eval_runs`, `runtime.tool_approvals`; read `logs.*` |
| `console_platform` (Phalcon, platform_admin) | As `console_app` + cross-company read policies |
| `ai_app` (ai-runtime, ai-worker) | Read/write `runtime.*` and `logs.*`; read views `console.v1_published_agents`, `v1_deployments`, `v1_tools`, `v1_egress_allowlist`, `v1_companies`, `v1_published_skills`, `v1_knowledge_bases`; execute `resolve_api_key` |
| `gateway_app` (ai-gateway) | As `ai_app` without KB/eval privileges |

Every table with `company_id` has `FORCE ROW LEVEL SECURITY` and indexes leading on `company_id`.

### 8.2 Tables

**Schema `console` (Phalcon)**

| Table | Key columns |
|-------|-------------|
| `companies` | id, code, name, external_ref, status, monthly_budget_usd |
| `users` | id, external_sub, email, is_platform_admin |
| `company_members` | company_id, user_id, role |
| `connections` | id, company_id, name, type, api_base, masked_hint, max_concurrency, meta JSONB (no secret) |
| `api_key_scopes` | api_key_id, deployment_id (same company — enforced by constraint) |
| `agents` | id, company_id, name, description, status, archived_at |
| `agent_versions` | id, company_id, agent_id, version_no, spec JSONB, spec_version, compiled_definition JSONB, compiler_version, dynamiq_version, is_published, published_by, published_at |
| `tools` | id, company_id, name, kind (http/builtin/python), access_level, auth_mode (service/delegated), audience, config JSONB, enabled |
| `knowledge_bases` | id, company_id, name, embedder, chunk_size, chunk_overlap, retrieval_mode (vector/hybrid), alpha, okf_field_map JSONB |
| `skills` | id, company_id, name, description, status |
| `skill_versions` | id, company_id, skill_id, version_no, object_key, content_hash, has_scripts, is_published |
| `deployments` | id, company_id, slug, environment, agent_version_id, rate_limit_per_min, daily_token_limit, daily_cost_limit_usd, allowed_origins, guardrail_overrides JSONB, output_mode, allow_write_tools, conversation_ttl_days, config_version, enabled |
| `api_keys` | id, company_id, name, key_hash, prefix, allowed_ips, rate_limit_per_min, daily_cost_limit_usd, last_used_at, created_by, revoked_at |
| `datasets` / `dataset_items` | id, company_id, name / dataset_id, question, expected_answer, context, tags |
| `egress_allowlist` | id, company_id (null = platform), host_pattern, port, allow_private_ip |
| `model_pricing` | provider, model, input_per_1k, output_per_1k |
| `settings` | key, value JSONB |
| `audit_logs` | id, company_id, user_id, action, entity, entity_id, diff JSONB, created_at |

**Schema `runtime` (Python)**

| Table | Key columns |
|-------|-------------|
| `agent_memory` (Dynamiq) | managed by the Dynamiq PostgreSQL memory backend — `user_id` = `{company_id}:{external_user_id}` |
| `company_keys` | company_id, wrapped_dek, master_key_version, created_at, destroyed_at (no access for console_app) |
| `connection_secrets` | connection_id, company_id, ciphertext, nonce, dek_version (no access for console_app) |
| `conversations` | id, company_id, deployment_id, source, external_user_id (hashed), last_message_at, expires_at |
| `kb_documents` | id, company_id, kb_id, okf_id, path, category, frontmatter JSONB, object_key, content_hash, status, error, chunk_count |
| `kbv_{company_id}_{kb_id}` | per-KB vector table (pgvector, created by Dynamiq) |
| `tool_approvals` | id, company_id, run_id, tool_id, request JSONB, status, decided_by, decided_at |
| `eval_runs` | id, company_id, agent_version_id, dataset_id, metrics JSONB, judge_model, status, est_cost_usd, summary JSONB |

**Schema `logs` (Python)** — monthly partitions, 90-day default retention

| Table | Key columns |
|-------|-------------|
| `runs` | id, company_id, agent_version_id, deployment_id, conversation_id, source, status, agent_name, model, input, output, tokens_in, tokens_out, cost_usd, guardrail_cost_usd, latency_ms, trace_id, error, created_at |
| `run_steps` | id, run_id, seq, type, payload JSONB |
| `guardrail_events` | id, company_id, run_id, stage, check, action, detail JSONB |
| `eval_results` | id, company_id, eval_run_id, dataset_item_id, run_id, scores JSONB, reasons JSONB |

## 9. API

Every `/admin/v1/*` route (except `/me`, `/companies`) requires `X-Company-Id`.

### 9.1 console-api (Phalcon) — `/admin/v1`

| Prefix | Role | Key endpoints |
|--------|------|---------------|
| `/me` | any user | Current user + companies and roles |
| `/runtime-token` | developer | Issue a Playground runtime token |
| `/connections` | admin | CRUD, `PUT /{id}/secret` and `POST /{id}/test` (forwarded internally) |
| `/agents` | developer | CRUD, clone, versions, `POST /versions/{vid}/publish` (compiled internally), export/import |
| `/deployments` | developer | CRUD, promote |
| `/api-keys` | admin | CRUD for the company's gateway API keys, per-deployment scope, revoke |
| `/runs` | viewer | List/detail, conversations, guardrail events (from schema `logs`) |
| `/dashboard` | viewer | Summary figures and charts |
| `/tools` · `/datasets` · `/evals` · `/approvals` | developer | CRUD + internal actions (phases 2–3) |
| `/kb` | developer | KB CRUD, import `.md`/`.zip`, edit OKF files, export `.zip`, search test (phase 2) |
| `/skills` | developer (skills with scripts: admin) | CRUD, versions, upload `.zip`, publish (phase 2) |
| `/settings` | admin | Company users, egress allowlist, budget |
| `/companies` | platform_admin | CRUD, suspend, members, DEK destruction on deletion |

### 9.2 ai-runtime internal (Python) — `/internal/v1` on port 8081 (console-api only)

| Endpoint | Purpose |
|----------|---------|
| `POST /agents/compile` | Check the agent spec + role-based node-type allowlist, compile to a Dynamiq definition, validate with Dynamiq; return error paths or `compiled_definition`, `compiler_version`, `dynamiq_version` |
| `POST /agents/recompile-all` | Recompile every published version (for Dynamiq/compiler upgrades); return failures |
| `PUT /connections/{id}/secret` · `POST /connections/{id}/test` | Store an encrypted secret; test a connection |
| `POST /kb/{id}/documents` · `POST /kb/{id}/search` · `GET /kb/{id}/export` | Enqueue OKF indexing (converting other formats to OKF first); test search; build export |
| `POST /evals/estimate` · `POST /evals` · `POST /evals/{id}/cancel` | Estimate cost, create an eval run, cancel |
| `POST /approvals/{id}` | Approve/reject a `write` tool call from the console |

### 9.3 ai-runtime (Playground) — `/ai/v1`

| Endpoint | Auth | Purpose |
|----------|------|---------|
| `POST /playground/run` · `POST /playground/stream` | runtime token | Run a given agent version (sync / SSE) |
| `POST /runs/{id}/cancel` | runtime token | Cancel a run |

### 9.4 ai-gateway (public) — `/v1/deployments/{slug}`

| Endpoint | Auth | Purpose |
|----------|------|---------|
| `POST /run` · `POST /stream` | API key or session token | Run the published agent (sync / SSE) |
| `POST /session` | API key | Issue a browser session token |
| `GET /info` | API key or session token | The agent's `display_name`, `role`, `languages` |
| `POST /approvals/{id}` | API key or session token | Approve a `write` tool call from the app |
| `GET /.well-known/jwks.json` (gateway root) | none | Public keys for verifying delegated identity tokens |

`/healthz`, `/readyz`, `/metrics` exist on every service but are never exposed via ingress.

## 10. Repository Structure

```
dynamiq-console/
├── contracts/openapi/          # console-api.yaml, ai-internal.yaml, ai-public.yaml
├── db/                         # roles, grants, v1_* views, RLS, SECURITY DEFINER + contract tests
├── console-api/                # Phalcon (PHP)
│   ├── app/
│   │   ├── controllers/admin/  # agents, connections, deployments, runs, dashboard, settings, companies
│   │   ├── models/             # Phalcon models (schema console)
│   │   ├── services/           # AuthService (SSO/JWT), CompanyContext, RuntimeClient, TokenIssuer
│   │   └── middleware/         # auth, company context + RLS, audit, traceparent
│   ├── db/migrations/          # Phinx: tables in schema console
│   └── tests/
├── ai/                         # Python — one image for ai-runtime / ai-gateway / ai-worker
│   ├── pyproject.toml, uv.lock # pin dynamiq==0.65.0
│   ├── alembic/                # runtime/ and logs/
│   ├── app/
│   │   ├── main_runtime.py     # /ai/v1 + /internal/v1
│   │   ├── main_gateway.py     # /v1/deployments
│   │   ├── worker.py           # ARQ
│   │   ├── core/               # config, db, auth (tokens), crypto, redaction, otel
│   │   ├── services/           # runtime, compiler, conversations, guardrails, quotas, identity,
│   │   │                       # kb_indexer, eval_runner
│   │   ├── integrations/       # dynamiq_adapter, safe_http_client, skill_registry, okf, thai_tokenizer
│   │   └── jobs/               # index_document, run_eval, cleanup_*
│   └── tests/
├── web/                        # SvelteKit
│   └── src/lib/api/            # TS clients generated from contracts/openapi
├── deploy/
│   ├── docker/                 # console-api (nginx + php-fpm), ai, web
│   └── k8s/                    # kustomize base/ + overlays/{staging,production}
└── docs/prd/
```

## 11. Infrastructure (kube-advws)

- Namespace `ai-console`; deployments: `console-web` (2), `console-api` (2), `ai-runtime` (2), `ai-gateway` (2–6, HPA), `ai-worker` (1–4, HPA on queue length).
- **PostgreSQL:** CloudNativePG cluster (primary + at least one replica, automatic failover); PgBouncer in transaction pooling mode in front of the primary; the replica serves heavy dashboard queries.
- **Redis:** 3-node Sentinel (ARQ queue, rate limits, quotas, semaphores); Redis-down behavior per 6.3.
- Ingress: internal (VPN) for `console-web`, `console-api` and `ai-runtime` port 8080 (`/ai/v1`); public with WAF/TLS for `ai-gateway`; port 8081 (`/internal/v1`) is on no ingress and NetworkPolicy admits only `console-api` pods.
- `terminationGracePeriodSeconds: 150` for ai-runtime/ai-gateway; proxies/ingress use read timeouts ≥ 180 s and disable response buffering for SSE.
- Secrets: `CONSOLE_MASTER_KEY`, per-role DB credentials, `REDIS_URL`, `MINIO_*`, SSO JWKS URL, per-issuer private keys (7.6).
- **Backup & DR:** daily base backup + WAL archiving (7-day PITR) of the whole `ai_console` database, 14-day retention; MinIO versioning + off-cluster replication; RPO ≤ 1 h, RTO ≤ 4 h, quarterly restore tests; `CONSOLE_MASTER_KEY` and private keys backed up separately from DB backups.

## 12. Edge Cases

- **Postgres failover:** the gateway keeps serving from cached config; run logs that fail to write are retried from an in-memory buffer (up to 60 s).
- **Redis down:** rate limits fail open, quotas fail closed for deployments with daily budgets, eval/index queues pause.
- **Agent spec with a node type outside the allowlist (e.g. raw JSON sent to the API):** compilation fails and an audit entry is written.
- **Phalcon down:** the gateway and open Playground sessions keep working because Python reads configuration from Postgres directly; the console is temporarily unavailable.
- **Dynamiq upgrade changes the schema:** version pinned; upgrade the compiler and run `recompile-all` on staging first — specs stay unchanged.
- **Connection deleted while in use:** deletion blocked, dependent agents listed.
- **Invalid/expired LLM key:** run fails with `connection_auth_failed` + warning on Connections.
- **Endless agent loop / pod restart mid-stream / client closes SSE:** recorded as `truncated` / `interrupted` / `cancelled` with tokens used; the conversation can continue.
- **Daily quota exhausted:** `429 quota_exceeded` with reset time; Playground is not counted.
- **Origin not in allowed_origins:** blocked at browser preflight; server-to-server calls with an API key work normally.
- **Guardrail detector down:** follows `on_detector_error`; false positives are switched to `flag` via a new version.
- **SSRF / non-allowlisted host:** `egress_blocked` + audit entry.
- **User removed from a company / JWT company not yet registered:** 403 / company skipped + audit.
- **Company A's API key calls company B's slug or an out-of-scope deployment:** 404 as if the deployment did not exist + audit entry.
- **Agent spec references another company's KB/connection/skill (e.g. raw ids via the API):** compilation fails + audit entry.
- **Company deletion:** suspend → destroy DEK (secrets become undecryptable immediately) → purge data and MinIO files on schedule.
- **OKF file without frontmatter or missing fields:** defaults apply (`id` from path, `title` from first H1) with a warning; a duplicate `id` within a KB rejects that file.
- **Skill missing or unpublished while in use:** `SkillsTool` returns an error to the agent and the trace records it; agent versions referencing a deleted skill fail validation on the next publish.
- **Target system rejects a delegated token (expired/clock skew):** 60-second tokens with 30-second skew tolerance; a 401 becomes a tool error — never retried with a service token.
- **Scanned-image KB document:** status `failed` — "no extractable text" (no OCR).

## 13. Testing

| Scenario | Expected |
|----------|----------|
| SSO login via the existing system | Access granted with companies and roles from the claim |
| Connection → agent → test → publish → deploy | Completed within 10 minutes; version immutable |
| Invalid config | Save rejected; failing path highlighted |
| Playground SSE | Tokens stream; tool_call/tool_result and guardrail steps appear |
| Model selection + fallback | Primary returns 5xx, run succeeds on fallback; trace records it |
| No secret leakage | No plaintext key in responses, logs or schema console |
| Company isolation | User of A cannot access B's agents (404/403); direct RLS query returns only own rows; A's API key cannot call B's slug |
| API key and knowledge separation | (1) A's key calls B's slug (2) A's key scoped to deployment X calls A's Y (3) A's agent references B's KB (4) search A's KB as a B user | (1) 404 (2) 404 (3) compilation fails (4) no results |
| Crypto-shredding | Destroy company A's DEK | A's secrets cannot be decrypted; other companies unaffected |
| Multi-company user | Switching companies changes permissions per role |
| Gateway: rate limit / quota / CORS | 429 / `429 quota_exceeded` / preflight blocked |
| Guardrails: prompt injection / PII / SSE block | Blocked + fallback / masked in LLM and logs / stream stops with `guardrail` event |
| SSRF | `169.254.169.254`, `kubernetes.default.svc`, non-allowlisted hosts blocked |
| Path separation | `/admin/v1` and `/internal/v1` unreachable publicly; `/v1/deployments` works |
| Concurrency + graceful shutdown | 429 beyond cap, `/healthz` still responds; rollout does not cut in-flight streams |
| Phalcon down | Gateway still serves |
| Node-type allowlist | A developer submits a spec with a Python tool, or an inline connection whose `api_base` targets an internal host | Compilation fails in both cases + audit entry |
| RLS hardening | (1) query as `console_app` without `app.company_id` (2) reuse a PgBouncer connection after company A's transaction (3) owner check | (1) no rows (2) none of A's rows (3) no app role owns a table |
| Cross-company memory | Same external_user_id in two companies | Memories do not mix |
| No PII leak mid-stream | Make the agent emit a national ID early in its answer | Client receives the masked value from the first chunk |
| Concurrent quota | 20 simultaneous requests near the daily budget | Actual spend does not exceed the budget (reservations) |
| DB contract test (CI) | Change a column in a `v1_*` view | CI fails |
| Postgres failover | Trigger failover under traffic | Gateway keeps serving; no logs lost |
| Internal port | Call port 8081 from a pod other than console-api | Rejected by NetworkPolicy |
| Backup restore | Restore into staging works within RTO |
| Identity: delegated (phase 2) | User A asks "my orders" through an agent calling the Phalcon system | Phalcon receives a JWT with `sub`=A and returns only A's orders; B's identity in the body of A's session-token request has no effect |
| OKF import (phase 2) | Upload a `.zip` of `.md` files with frontmatter | Metadata filters work, chunks keep heading paths, editing one file re-indexes only that file, export returns the same files |
| Thai hybrid search (phase 2) | Search by product code and unspaced Thai terms | Hybrid returns the correct document in the top 3 |
| Agent identity | Set `out_of_scope`, ask an out-of-scope question, then trigger a `handoff` condition | The agent declines politely / states the configured handoff channel; the catalog shows the correct role and responsibilities |
| Skills (phase 2) | Attach a skill and ask something requiring it | Trace shows `SkillsTool` list → get and the answer follows the skill |
| 50-item eval (phase 3) | Cost estimate before run, per-item results + means, Compare and Publish gate work |

## 14. Phases & MVP Tasks

| Phase | Scope | Estimate |
|-------|-------|----------|
| 1 — MVP | SSO + multi-company + hardened RLS, agent spec + compiler, Connections, Agents + Versions + model/fallback, Guardrails, Playground (SSE), Deployments + Gateway (quota, CORS), Conversations, Runs/Dashboard, egress control, Postgres/Redis HA, backup | ~6 weeks (3 devs: PHP, Python, Web) |
| 2 | Tools (HTTP + built-in) + tool approval, identity propagation (delegated), OKF Knowledge Bases + hybrid (Thai-aware), Skills (instructions only, no scripts) | ~4 weeks |
| 3 | Evaluation, skills with scripts (sandboxed), Environments + Promote + YAML export/import, per-company monthly budget, Grafana Tempo | 3–3.5 weeks |
| 4 | Workflows (YAML editor), knowledge graph (optional), audit log UI, move traces to ClickHouse if needed | 2–3 weeks |

**MVP tasks**

| # | Task | Track | Days |
|---|------|-------|------|
| 1 | Repo, three OpenAPI contracts, CI | shared | 2 |
| 2 | Postgres: `db/` (roles, grants, `v1_*` views, FORCE RLS + `SET LOCAL`, `resolve_api_key`) + contract tests + Phinx (console) + Alembic (runtime, logs) | shared | 4 |
| 3 | SSO JWT verification + companies/members sync + company context + RLS session | PHP | 2.5 |
| 4 | Existing Phalcon system: `companies` claim + JWKS | PHP | 1.5 |
| 5 | CRUD: connections (meta), agents/versions, deployments, company API keys + scopes, settings/allowlist, companies | PHP | 5 |
| 6 | RuntimeClient (internal JWT, traceparent) + runtime token issuance + console-api JWKS/`kid`/rotation | PHP | 2 |
| 7 | Dashboard + Runs (queries on schema `logs`, from the replica) | PHP | 2 |
| 8 | Agent spec (incl. identity + prompt template) + compiler + node-type allowlist + cross-company reference checks, internal API (port 8081): compile, recompile-all, secrets (per-company DEK envelope encryption) + connection test, cache invalidation (NOTIFY + `config_version`) | Python | 7 |
| 9 | Runtime: run/stream via `run_async` + semaphore, heartbeat, retry/fallback, graceful shutdown, tracing → logs (+ buffer during DB failover), cost | Python | 3.5 |
| 10 | Conversations + Dynamiq memory (`runtime.agent_memory`, per-company namespace) | Python | 1.5 |
| 11 | Guardrail engine (input/output, block/mask/flag, per-chunk fast checks + windowed model checks, buffered default, detector cost accounting) | Python | 3 |
| 12 | Gateway: API keys (+ `allowed_ips`), session tokens (embedding end-user identity), `resolve_api_key`, rate limit, reservation-based quota, Redis failure policy, CORS, config cache | Python | 3.5 |
| 13 | SafeHttpClient + egress allowlist | Python | 1.5 |
| 14 | SvelteKit scaffold, auth, company switcher, API clients | Web | 2 |
| 15 | Connections, API Keys, Settings, Companies pages | Web | 3 |
| 16 | Agents (catalog + tabs + Identity + Monaco + Guardrails) + Versions | Web | 5 |
| 17 | Playground (SSE + Trace) | Web | 2.5 |
| 18 | Runs + Dashboard | Web | 2 |
| 19 | Deployments | Web | 1 |
| 20 | Dockerfiles + K8s (two ingresses, port-based NetworkPolicy, HPA, SSE proxy settings) + CloudNativePG HA + PgBouncer + Redis Sentinel + backup + staging deploy | shared | 4.5 |
| 21 | Section 13 tests + one-agent pilot | shared | 2.5 |
|   | **Total** (PHP ~13, Python ~20, Web ~15.5, shared ~13) | | **~61.5 working days** |

Three developers in parallel (PHP / Python / Web) sharing the common work ≈ 6 weeks (the Python track is longest — PHP/Web developers take more of the shared infra work); two developers ≈ 7.5 weeks.

**Phase 2 — main work (estimate)**

| Work | Track | Days |
|------|-------|------|
| Tools CRUD + approval queue + `auth_mode` | PHP / Web | 4 |
| Tool execution + approval flow + delegated identity tokens + JWKS | Python | 3.5 |
| Existing Phalcon system: verify delegated JWTs and authorize by `sub` on tool endpoints | PHP | 2 |
| OKF: frontmatter reader + field map, `.zip` import, other formats → Markdown, hash-based re-index, export, per-company MinIO paths | Python | 4 |
| Hybrid retrieval + Thai tokenization (`search_text`) | Python | 2 |
| KB pages (import, Markdown editor, search test, export) | PHP / Web | 4 |
| Skills: `ConsoleSkillRegistry` + `SkillsTool` wiring | Python | 2 |
| Skills pages + Skills tab in the agent editor | PHP / Web | 3 |
| Tests + pilot | shared | 2 |
|  | **Total** | **~26.5 working days** (3 devs ≈ 4 weeks) |

## 15. Open Questions

1. Is `console-api` a separate Phalcon app or a module inside the existing app, and which Phalcon/PHP versions does the existing system use?
2. Does the existing Phalcon system already issue JWTs/JWKS, and does it store user–company relationships for the `companies` claim?
3. Are HA PostgreSQL 16 (+pgvector) (or the CloudNativePG operator), Redis Sentinel and MinIO available on kube-advws, or must they be provisioned?
4. How many companies at launch, and does each already have its own LLM API keys (the system provides no shared central key)?
5. Primary LLM provider and initial monthly budget.
6. First pilot use case and which existing-system endpoints to expose as tools.
7. Retention policy for logs and personal data (PDPA) — must PII be masked before logging?
8. Does kube-advws already provide an egress proxy, WAF and Grafana Tempo?
9. The first `write` tool (if any) and who approves it.
10. The OKF (Open Knowledge Format) spec: required/optional frontmatter fields, cross-file link format, and whether it carries versioning or access metadata — the draft in 6.6 applies until then.
11. Can the existing Phalcon system verify delegated JWTs and authorize per user on the endpoints to be exposed as tools?
12. The first knowledge content and skills to import (e.g. machine manuals, refund policy, FAQ).

## Revision History

| Rev | Changes |
|-----|---------|
| r10 | Goal 9 redefined as Agent Identity (what the agent is for) + section 6.1a, Identity tab, agent catalog, gateway `GET /info`; end-user identity (7.5) remains under security |
| r9 | Strict per-company API keys and knowledge (7.7): shared connections removed, per-company DEK envelope encryption + crypto-shredding, company-owned gateway API keys with per-deployment scopes and an API Keys page, per-company MinIO prefixes, compiler cross-company reference checks, platform_admin limited to aggregate figures |
| r8 | Applied the 14-point architecture review: agent spec + compiler + node-type allowlist, FORCE RLS + `SET LOCAL` + `resolve_api_key`, per-company memory/vector namespacing, Postgres/Redis HA, logs as a schema in the single DB, mid-stream fast PII checks + buffered default, cache `config_version` checks, key/token plan (7.6), `db/` owning views/grants + contract tests, internal API on a separate port, server-only API keys + `allowed_ips`, per-connection concurrency, guardrail cost accounting, reservation-based quotas, `run_async` |
| r7 | Added OKF knowledge (Markdown + frontmatter) + Thai-aware hybrid search, Skills (`SKILL.md`), end-user identity propagation (delegated JWT), Goals 8–9, phase 2 plan |
| r6 | Full rewrite: Phalcon console backend + Python for AI, single PostgreSQL (console/runtime schemas + logs DB), request flows, four API groups, tasks by track |
| r5 | Multi-company; visual workflow builder removed |
| r4 | New Goals; Content Guardrails |
| r3 | Admin/public path split, quotas, CORS, backup, repo structure, API map |
| r2 | Conversations, execution model, gateway, egress/SSRF, tool levels, logs DB, environments, OpenTelemetry |
| r1 | Initial version |
