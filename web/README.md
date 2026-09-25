# dynamiq-console / web

SvelteKit (Svelte 5, `adapter-static`) skeleton for the Dynamiq AI Management Console front end. See `docs/prd/prd-001-dynamiq-ai-console.en.md` and `docs/prd/mockup-001-dynamiq-ai-console.md` for product context.

## Getting started

```bash
npm install
npm run dev
```

The dev server defaults to `http://localhost:5173`.

To produce the static build (served by any static file host / CDN, `fallback: index.html` for SPA-style client routing on dynamic routes):

```bash
npm run build
```

Output goes to `build/`.

Copy `.env.example` to `.env` and set `PUBLIC_CONSOLE_API_BASE_URL` to point at `console-api` (Phalcon), e.g. `http://localhost:8000/admin/v1`.

## `src/lib/api` naming convention

Everything in `src/lib/api/console.ts` is **hand-written today, but named to match `contracts/openapi/console-api.yaml` exactly** — every exported function name is the `operationId` of the corresponding path in that spec (e.g. the `GET /agents` operation has `operationId: listAgents`, so the client exports `listAgents()`).

This is deliberate: once the contract stabilizes, this file is meant to be **replaced by codegen** (e.g. `openapi-typescript` + a thin fetch wrapper, or `orval`) driven directly off `../../contracts/openapi/console-api.yaml`, without touching call sites elsewhere in the app — every `import { listAgents } from '$lib/api/console'` keeps working unchanged. See `src/lib/api/README.md` for more detail.

`client.ts` holds the one hand-written piece that survives codegen: `apiFetch()`, a fetch wrapper that injects `PUBLIC_CONSOLE_API_BASE_URL` as the base URL and `X-Company-Id` from the current company store on every request (PRD §7.2 — every `/admin/v1/*` request except `/me` and `/companies` requires that header).

## Structure

- `src/lib/api/` — typed API client (`client.ts` fetch wrapper, `console.ts` operationId-named functions)
- `src/lib/stores/` — `company.ts` (current company, persisted to localStorage), `auth.ts` (current user/session stub)
- `src/lib/components/layout/` — `Sidebar.svelte`, `Header.svelte`, `CompanySwitcher.svelte`
- `src/routes/` — one route per PRD §6.9 phase-1 menu item; phase 2+ items (Tools, Knowledge Bases, Skills, Evaluation, Workflows) are nav-only stubs in `Sidebar.svelte`, not routed pages, until their phase starts
