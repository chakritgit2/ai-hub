# OpenAPI Contracts

Source of truth for API shape across all three services. Every path has an explicit
`operationId` using the convention `verbResourceOptionalSub` (camelCase), e.g. `listAgents`,
`createAgent`, `publishAgentVersion`, `runPlaygroundStream`.

**That `operationId` is reused verbatim** as:
- the controller method name in `console-api/app/controllers/admin/*.php`
- the FastAPI route function name in `ai/app/main_runtime.py` / `ai/app/main_gateway.py`
- the exported function name in `web/src/lib/api/console.ts`

so grepping one identifier finds the same endpoint in all three codebases.

Files:
- `console-api.yaml` — Phalcon control-plane API, `/admin/v1/*` (PRD §9.1)
- `ai-internal.yaml` — `ai-runtime` internal API, `/internal/v1/*`, console-api-only (PRD §9.2)
- `ai-public.yaml` — `ai-runtime` Playground API (`/ai/v1/*`) and `ai-gateway` public API (`/v1/deployments/*`) (PRD §9.3-9.4)

Phase-1 endpoints have full request/response schemas. Phase 2/3 endpoints exist (so routing/naming
is settled now) but use a generic placeholder body and are tagged `x-phase: 2` or `x-phase: 3`.

TS client codegen tool (openapi-typescript / orval / openapi-generator-cli) is not yet chosen —
`web/src/lib/api/console.ts` currently hand-writes stub functions named after these operationIds.
