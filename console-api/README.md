# console-api

Control-plane API for the Dynamiq AI Management Console (PRD-001). Phalcon 5 / PHP 8.1,
serving every `/admin/v1/*` route defined in
[`contracts/openapi/console-api.yaml`](../contracts/openapi/console-api.yaml).

This is a bootable **skeleton**: routing, DI, config, migrations and middleware are real;
almost every controller action returns `501 {"error": "not_implemented"}` until the
corresponding business logic (the agent compiler, guardrails, quotas, ...) is built out.
`GET /healthz` is fully real; `GET /me`, `GET /admin/.well-known/jwks.json` and every other
`/admin/v1/*` route now require a verified SSO JWT (see `AuthService`/`AuthMiddleware`
below). `RuntimeClient`/`TokenIssuer` are real too — console-api can mint and verify its
own runtime/internal tokens and call ai-runtime's `/internal/v1/*` — but every mutating
admin controller (Agents, Connections, Deployments, ...) still 501s past auth; `/me` and
`/runtime-token` are the two that do something real.

## Route -> operationId -> controller method mapping

Every path in `console-api.yaml` has an `operationId`; that string is **exactly** the
controller method Phalcon dispatches to — there is no `Action` suffix
(`$dispatcher->setActionSuffix('')` in `app/config/services.php`), so
`operationId: getMe` really is `MeController::getMe()`.

Routes are grouped into one controller per first path segment, matching the OpenAPI tag
grouping:

| First path segment | Controller | Namespace |
|---|---|---|
| `/me` | `MeController` | `ConsoleApi\Controllers\Admin` |
| `/runtime-token` | `RuntimeTokenController` | `ConsoleApi\Controllers\Admin` |
| `/connections` | `ConnectionsController` | `ConsoleApi\Controllers\Admin` |
| `/agents` | `AgentsController` | `ConsoleApi\Controllers\Admin` |
| `/deployments` | `DeploymentsController` | `ConsoleApi\Controllers\Admin` |
| `/api-keys` | `ApiKeysController` | `ConsoleApi\Controllers\Admin` |
| `/runs` | `RunsController` | `ConsoleApi\Controllers\Admin` |
| `/dashboard` | `DashboardController` | `ConsoleApi\Controllers\Admin` |
| `/settings` | `SettingsController` | `ConsoleApi\Controllers\Admin` |
| `/companies` | `CompaniesController` | `ConsoleApi\Controllers\Admin` |
| `/tools` (phase 2) | `ToolsController` | `ConsoleApi\Controllers\Admin` |
| `/datasets` (phase 3) | `DatasetsController` | `ConsoleApi\Controllers\Admin` |
| `/evals` (phase 3) | `EvalsController` | `ConsoleApi\Controllers\Admin` |
| `/approvals` (phase 2) | `ApprovalsController` | `ConsoleApi\Controllers\Admin` |
| `/kb` (phase 2) | `KbController` | `ConsoleApi\Controllers\Admin` |
| `/skills` (phase 2) | `SkillsController` | `ConsoleApi\Controllers\Admin` |

Explicit route registration lives in `app/config/routes.php` (no annotations/attributes —
each `/admin/v1/*` path from the OpenAPI file is added one line at a time, with the
`{id}`/`{vid}` path params declared as `{id:[^/]+}`). `GET /healthz` is the one route that
isn't `/admin/v1/*` and isn't in the OpenAPI file — it's an ops-only liveness probe.

`x-phase: 2` and `x-phase: 3` operations (Tools, Datasets, Evals, Approvals, KB, Skills,
plus a few individual operations like `exportAgent`/`promoteDeployment`) get a real route
and a real controller method; they just return 501 for now so the API surface is stable
for `web/`'s generated TS client from day one.

## Request lifecycle

`public/index.php` boots a `Phalcon\Mvc\Application` (not Micro) with:

1. **Router** (`app/config/routes.php`) — explicit routes only, default routing disabled.
2. **Dispatcher** — default namespace `ConsoleApi\Controllers\Admin`, no action suffix.
3. **Middleware**, attached as dispatcher events:
   - `TraceparentMiddleware` (`dispatch:beforeDispatch`) — reads/generates the W3C
     `traceparent` header for propagation to ai-runtime (PRD §6.9).
   - `AuthMiddleware` (`dispatch:beforeDispatch`) — **real**, except `GET /healthz`: requires
     `Authorization: Bearer <jwt>`, verifies it against `SSO_JWKS_URL` via
     `AuthService::verifySsoJwt()` (RS256 + JWKS, PRD §7.1/§7.6), then upserts
     `console.users`/`console.company_members` via `AuthService::syncUser()` and attaches
     the result to the dispatcher as `authUser` (read with `ControllerBase::getAuthUser()`).
     Invalid/missing tokens get a `401 {"error": "unauthorized"}`. The existing Phalcon
     system's real JWKS URL/claim shape is still open (PRD §15.2), so `SSO_JWKS_URL` /
     `SSO_ISSUER` / `SSO_AUDIENCE` are just env-configurable for now.
   - `CompanyContextMiddleware` (`dispatch:beforeDispatch`) — **real**, binds the
     `X-Company-Id` header into the shared `CompanyContext` service.
   - `AuditMiddleware` (`dispatch:afterDispatch`) — **real**: writes one `console.audit_logs`
     row per successful (2xx) mutating (`POST`/`PUT`/`PATCH`/`DELETE`) request, via
     `AuditLogger`. `AuthService::syncUser()` also calls `AuditLogger` directly for PRD §12's
     "company skipped" edge case (a `company_id: null` row, visible only to
     `console_platform` — see `db/migrations/post/002_rls_policies.sql`). `diff` is left
     null for now — no mutating controller captures before/after state yet.
4. Every controller action returns a `Phalcon\Http\Response` directly
   (`$application->useImplicitView(false)`), so there's no Volt/view layer to configure.
5. A top-level `try/catch` in `public/index.php` turns dispatcher/router failures into a
   JSON `404`, and anything else into a JSON `500` — the API never returns an HTML trace.

## Database & RLS

`app/services/CompanyContext.php` is the real implementation of PRD §7.3's mechanism:
`CompanyContext::applyToConnection($db)` runs `SET LOCAL app.company_id = '<id>'` inside
the current transaction (never a session-level `SET`, since PgBouncer runs in transaction
pooling mode). `FORCE ROW LEVEL SECURITY` and the actual policies are applied by
`../db/migrations/post/` (repo-level `db/`, not this service's job) — this skeleton only
sets the session variable that those policies read via `current_setting('app.company_id', true)`.

## Running locally

```bash
composer install
cp .env.example .env   # adjust DB_* if your local Postgres differs

# 1. db/migrations/pre/*.sql must have been applied already (creates the `console` schema
#    and roles) — see ../db/README.md.
vendor/bin/phinx migrate -c db/migrations/phinx.php -e local

# 2. serve — port 8000, matching web/.env.example's PUBLIC_CONSOLE_API_BASE_URL
#    and the root README's local dev instructions. The trailing public/index.php makes
#    it the router for every request — without it, PHP's built-in server 404s any path
#    with a dot in a segment (e.g. /admin/.well-known/jwks.json) before ever reaching it.
php -S localhost:8000 -t public public/index.php
curl localhost:8000/healthz   # {"status":"ok"} — does not touch the database

# every other route needs a valid SSO JWT once SSO_JWKS_URL is configured:
curl localhost:8000/admin/v1/me -H "Authorization: Bearer <jwt>"

# tests
vendor/bin/phpunit
```

## Directory layout

```
app/
├── config/        # config.php (env), services.php (DI), routes.php (router)
├── controllers/
│   ├── ControllerBase.php   # jsonResponse()/notImplemented()/getCompanyId()/getAuthUser()
│   └── admin/                # one controller per OpenAPI first-path-segment
├── models/         # Phalcon\Mvc\Model, schema `console`
├── services/       # AuthService, CompanyContext, RuntimeClient, TokenIssuer, AuditLogger,
│                   # ConsoleJwks — all real
└── middleware/      # Traceparent, Auth, CompanyContext, Audit — all real
db/migrations/       # Phinx — tables in schema `console`
tests/               # PHPUnit
```
