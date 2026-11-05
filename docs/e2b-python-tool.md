# `kind: python` tools (E2B sandboxed code execution)

Reference doc for how the `python` tool kind actually works end to end - written up
2026-11-05 after a live debugging session against the real `ai-hub-advws` cluster and the
team's self-hosted E2B instance. See PRD §6.1/§6.6a for the original requirement.

## 1. What it is, in one sentence

A `python` tool gives an Agent the ability to write Python code on the fly and have it
**actually executed** by a real sandboxed computer (E2B), instead of the LLM just guessing
an answer in text. It is a generic capability, not a single fixed action - one tool is
enough for calculations, data processing, CSV summaries, or any other code-shaped task;
you do not need a separate tool per use case.

## 2. Architecture - who does what

```
User message
    │
    ▼
Agent (the LLM, e.g. gpt-4o-mini)
    - decides per turn whether this request needs code execution
    - if yes, writes the Python source itself, fresh every time
    - the Tool's own config (timeout/domain/etc.) never influences *what* code gets
      written - only the agent's own instructions + conversation context do
    ▼  (sends the code string via a tool call)
Tool (console.tools row, kind=python)
    - NOT an agent, NOT intelligent - a passive connector only
    - wraps the connection details to one specific E2B instance
    ▼  (forwards the code as-is)
E2B Sandbox (a real remote machine - self-hosted at 192.168.33.200 for this team)
    - the only place code is actually processed
    - runs the code, returns stdout/stderr/result back up the chain
```

Key point: **no AI model exists anywhere below the Agent layer.** The Tool and the
Sandbox are pure mechanism - running code requires no intelligence.

## 3. Why there are separate tool kinds at all (`http` / `builtin` / `python`)

It would be technically possible to let the agent's Python code make its own HTTP calls
instead of using a `kind: http` tool, but that is deliberately not done:

- `kind: http` tools are checked against the **egress allowlist** before every call
  (`app/integrations/safe_http_client.py`) - code running freely in a sandbox would bypass
  that check entirely.
- `kind: python` always requires human approval on every single call regardless of
  `access_level` (`app/integrations/safe_python_tool.py`) - using it for every trivial
  lookup would make routine `http`-shaped work painfully slow.
- `kind: http`'s `auth_mode: delegated` cleanly signs a per-end-user identity JWT
  (PRD §7.5) - freshly-generated code could never replicate that safely.
- A fixed `http` tool's behavior is auditable from its config alone; freshly-generated
  code is not.

## 4. Who writes what code, and when

The Agent decides **per message**, not once at setup, whether to call the tool at all.
Nothing forces it to use the tool for every turn - a greeting gets a plain text reply,
no tool call, no approval prompt. It writes the actual Python source as part of its own
output (the same generative mechanism as any other LLM text response, just captured as a
structured tool-call argument instead of a chat message) - there is no template, no
stored "code snippet", and no config field anywhere that influences what the code looks
like.

To make the agent reach for this tool reliably, write guidance in the **Agent's own
Instructions** (Identity tab), e.g. "use the python tool for any calculation or data
summary request" - the tool itself has no field for this purpose beyond the optional
`description` note (see below), which currently only supplements, not replaces, that
guidance role.

## 5. Per-tool configuration (Tools page, kind = python)

| Field | Effect |
|---|---|
| Name | Shown to the LLM as the tool's identifier. |
| Access level | Read or Write - has almost no practical effect here, since every python call requires approval regardless (see §6). |
| Description (optional) | An admin's own "what this is for" note. Shown on the Tools list for humans, and appended **after** (not instead of) Dynamiq's fixed sandbox-usage text the agent still needs technically (`compiler.py::_build_python_tool`). |
| Timeout (seconds, 1-1800, default 600) | Per-call sandbox timeout. |
| Allow packages | Lets the agent `pip install` extra packages inside the sandbox. |
| Domain (advanced) | Forwarded to `dynamiq.connections.E2B.domain`. Only meaningful for a self-hosted E2B that follows E2B's `api.<domain>`/`sandbox.<domain>` subdomain convention - **not used by this team's own self-hosted setup** (see §7 - its API/sandbox URLs are unrelated hosts, set via env vars instead). Leave blank. |

The E2B API key is **never** entered here or in any `.env` file - it is a per-tool secret,
set via the "Set secret" button on the Tools page (`PUT /tools/{id}/secret`), decrypted at
run time per company (`app/services/tool_secrets.py`).

## 6. Security model

- Creating/editing/deleting a `kind: python` tool requires the **admin** role (every other
  kind allows admin or developer) - `ToolsController::requireWriteRoleForKind`.
- Attaching a `kind: python` tool to an agent version and publishing it also requires
  admin - enforced at compile/publish time via `app/services/node_allowlist.py`'s
  `ADMIN_ONLY_NODE_TYPES` (`E2BInterpreterTool` is in that list).
- Every single call the agent makes through the tool pauses for human approval
  (`app/integrations/safe_python_tool.py::SafePythonTool`), unconditionally - unlike
  every other `Safe*` tool wrapper, which only gates on `access_level: write`.

## 7. Self-hosted E2B instance (this team's own, not E2B's public cloud)

Connection settings, per the infra hand-off (2026-11-04/05):

```
E2B_API_URL=http://192.168.33.200:3000
E2B_SANDBOX_URL=http://192.168.33.200:3002
E2B_API_KEY=<one shared team key - set as a per-tool secret, never an env var>
Dashboard: http://192.168.33.200:3001
```

**Why these are environment variables on the pods, not the Tool's "Domain" field:**
`dynamiq`'s own `E2B` connection class only ever forwards `api_key`/`domain` to the e2b
SDK (`dynamiq/nodes/tools/e2b_sandbox.py::_create_sandbox`) - it never passes
`api_url`/`sandbox_url` explicitly. The e2b SDK's `ConnectionConfig` falls back to reading
`os.getenv("E2B_API_URL")`/`os.getenv("E2B_SANDBOX_URL")` directly when those aren't
passed in code. This team's self-hosted instance uses two unrelated hosts/ports for its
API and sandbox traffic (not E2B Cloud's `api.<domain>`/`sandbox.<domain>` subdomain
convention), so the "Domain" field cannot express it - real process env vars are the only
way.

Set in `deploy/k8s/base/ai-config.yaml` (the `dynamiq-ai-config` ConfigMap shared by
`ai-runtime`, `ai-gateway`, and `ai-worker` - all three can run agent/eval code that might
call a python tool). Applying a ConfigMap change does **not** restart pods automatically;
a new rollout (e.g. a Jenkins deploy) is needed to pick it up.

### 7.1 The template gotcha (already resolved, keep for reference)

`e2b_code_interpreter.Sandbox.create()` (what `E2BInterpreterTool` actually calls, not
plain `e2b.Sandbox`) requests a sandbox template named `code-interpreter-v1` by hardcoded
default (`e2b_code_interpreter/constants.py::DEFAULT_TEMPLATE`) - this is **not** the same
as the generic `base` template self-hosted E2B ships with out of the box. The symptom was
a clean `404: template 'code-interpreter-v1' not found` from a live Test call, even though
connectivity and the API key were both already correct. Fixed by infra building that exact
template from E2B's own open-source definition
(`github.com/e2b-dev/code-interpreter`, tag `@e2b/code-interpreter-template@0.4.7`) on the
self-hosted node - no application code change was needed once the template existed.

### 7.2 Known operational limits of this self-hosted instance

- **Languages**: Python and JavaScript kernels only - no R/Java/Bash via `run_code()`.
- **Capacity**: one shared 12 GiB memory pool for the whole team (not E2B Cloud's
  elastic capacity) - roughly 6 concurrent `code-interpreter-v1` sandboxes at full size.
  A `"500: Failed to place sandbox: this node cannot map ... of guest memory"` error means
  the pool is full; back off and retry rather than looping.
- **No TLS**, reachable only from the office LAN/VPN (private IP range).
- **Restarts**: the node is restarted nightly for backups - any sandbox still running at
  that time does not survive it.
- `sandbox.get_host(port)` returns a `*.e2b.app`-style hostname that does **not** resolve
  against this instance (only matters for raw port-proxying use cases - `run_code()`
  itself already handles this correctly via the sandbox proxy headers).

## 8. Troubleshooting quick reference

| Symptom | Likely cause |
|---|---|
| `sandbox connectivity failed: ...` (connection-level exception) | `E2B_API_URL`/`E2B_SANDBOX_URL` wrong or pod hasn't picked up a ConfigMap change yet (needs a rollout) |
| `sandbox reported an error: None` / `404: template 'code-interpreter-v1' not found` | Wrong E2B instance (pointing at E2B Cloud instead of self-hosted), or the template genuinely isn't built there yet |
| `no E2B API key secret configured for this tool` | "Set secret" was never called for this tool |
| `401` / invalid API key | Stale/rotated key - ask infra |
| `"cannot map ... guest memory"` | Capacity pool full - retry later, don't loop aggressively |
| Python tool checkbox greyed out in the Agent editor's Tools tab | Stale frontend allowlist bug, fixed 2026-11-04/05 (`AgentSpecTabs.svelte`'s `USABLE_TOOL_KINDS`) - if it reappears, check that constant still includes `'python'` |

## 9. Where the code lives

- `ai/app/services/compiler.py::_build_python_tool` - builds the real `SafePythonTool`
  node from a tool's config/secret at compile time.
- `ai/app/integrations/safe_python_tool.py` - the always-approval-gated wrapper subclass.
- `ai/app/services/node_allowlist.py` - admin-only node-type gating.
- `console-api/app/controllers/admin/ToolsController.php` - CRUD, secret storage, the
  live "Test" button (`testTool`), all kind-aware.
- `web/src/routes/tools/+page.svelte` - the Tools list/create/edit UI.
- `web/src/lib/components/agents/AgentSpecTabs.svelte` - the Agent editor's Tools
  attach tab (shared by the create/edit/version-edit agent pages).
- `deploy/k8s/base/ai-config.yaml` - the shared `E2B_API_URL`/`E2B_SANDBOX_URL` env vars.
