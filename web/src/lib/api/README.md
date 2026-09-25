# src/lib/api

Hand-written now; this whole directory (except `client.ts`) is meant to be **replaced by codegen** from `../../../contracts/openapi/console-api.yaml` once the contract stabilizes (e.g. `openapi-typescript` for `types.ts`, `orval` or a small custom generator for `console.ts`).

Until then:

- `types.ts` — TypeScript interfaces mirroring `components.schemas` in the OpenAPI file.
- `console.ts` — one exported function per OpenAPI path operation, **named exactly after that operation's `operationId`** (e.g. `GET /agents` → `operationId: listAgents` → `export function listAgents()`). Keeping this 1:1 means generated code can drop in later without call sites elsewhere in the app changing.
- `client.ts` — the fetch wrapper (`apiFetch`) that every generated/hand-written function call goes through; this file is expected to survive codegen unchanged, since generators are pointed at a wrapper like this rather than raw `fetch`.

Phase 2/3/4 operations (`x-phase` in the YAML) return a loose `Placeholder` type — their real request/response shapes aren't designed yet.
