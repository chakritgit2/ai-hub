# dynamiq-console → ai-hub-advws: handoff to infra

First production rollout of dynamiq-console (console-api, ai-runtime, ai-gateway, web) into
a **new, dedicated namespace `ai-hub-advws`** on the existing `cdi-advws-cluster` (confirmed
empty, created by infra ~minutes before this handoff was written — not shared with
operations-advws, which lives in its own `cdi-advws` namespace on the same cluster).
Everything below was prepared and verified from a dev machine with **read-only** cluster
access; it still needs someone with write access to actually apply it. See the session plan
for the full rationale on scope (single-replica Postgres/Redis, no HA yet, fake-sso.php as a
temporary login stand-in, MinIO/ai-worker deferred).

## What's already done

- **Three Dockerfiles fixed and build-tested locally** (`deploy/docker/ai`, `console-api`,
  `web` — all previously incomplete placeholders). `console-api`'s now reuses
  `phalconphp/cphalcon:v5.9.2-php8.4`, the same base image `operations-advws`'s own
  Dockerfile already uses successfully on this cluster.
- **k8s manifests written** under `deploy/k8s/base/`: `postgres-statefulset.yaml`,
  `redis-deployment.yaml`, `ai-config.yaml`, `ai-runtime-deployment.yaml`,
  `ai-gateway-deployment.yaml`, `console-api-deployment.yaml`, `web-deployment.yaml`,
  `fake-sso-deployment.yaml`, `secrets.example.yaml`. `kustomization.yaml` wires them
  together under `namespace: ai-hub-advws`.
- **Registry confirmed**: `cr.advws.com` — found by inspecting `fastapi-dynamiq`'s and
  `ai-hub-phalcon-php`'s existing Deployments **in the `cdi-advws` namespace**
  (`kubectl -n cdi-advws get deployment <name> -o yaml`), both of which pull via a
  `dockersecret` imagePullSecret. Every Deployment below already references
  `dockersecret` too — **but see blocker #2 below, it doesn't exist in the new namespace
  yet.** Postgres and Redis use plain public images (`pgvector/pgvector:pg16`,
  `redis:7-alpine`) — confirmed this cluster pulls public Docker Hub images fine (e.g.
  `mariadb`, `influxdb`, `nginx:alpine` are already running elsewhere on it), so no
  registry/credentials needed for those two.
- **DB bootstrap script**: `deploy/k8s/bootstrap-db.sh` — runs the schema/role setup, then
  Phinx (PHP migrations), then Alembic (Python migrations), then the `db/migrations/post/`
  SQL files, in the required order, via `kubectl exec` into the already-deployed pods (no
  extra migration image needed).
- **Naming checked for collisions**: confirmed `ai-hub-advws` is completely empty
  (`kubectl -n ai-hub-advws get pods,deployments,statefulsets,services` → "No resources
  found") — being a dedicated namespace, there's no collision risk with the ~15 unrelated
  apps that live in `cdi-advws`.

## What infra needs to do

1. **Grant write access.** The token used to prepare/verify the above
   (`system:serviceaccount:cdi-advws:dev-ingress-phalcon`) is read-only
   (`get/list/watch` on pods/services/deployments/statefulsets, plus `pods/exec`), and that
   read access only reaches `ai-hub-advws` because the role is cluster-scoped — it cannot
   create anything anywhere. Whoever applies this needs `create`/`update` on `deployments`,
   `services`, `statefulsets`, `secrets`, `configmaps`, `persistentvolumeclaims` in
   `ai-hub-advws` specifically.
2. **Create/copy the `dockersecret` imagePullSecret into `ai-hub-advws`.** imagePullSecrets
   are namespace-scoped — the one `cdi-advws` pods use does not automatically exist in the
   new namespace. Every app Deployment here (console-api, ai-runtime, ai-gateway, web,
   fake-sso) references it by name and will sit in `ImagePullBackOff` without it.
3. **Generate real secrets** (see `deploy/k8s/base/secrets.example.yaml` for the exact
   keys/commands) and `kubectl create secret` them directly in `ai-hub-advws` — never
   commit real values. Needed: 5 DB role passwords + 2 full `DATABASE_URL` DSNs,
   `CONSOLE_MASTER_KEY`, `HASH_PEPPER`, and two fresh RS256 keypairs (console-api's
   signing key, the gateway's own key).
4. **Fill in the two Jenkinsfile placeholders** (`REGISTRY_CREDENTIALS_ID`,
   `KUBECONFIG_CREDENTIALS_ID`) with whichever Jenkins credentials already hold push
   access to `cr.advws.com` and write access to `ai-hub-advws`.
5. **Check node affinity.** Deployments in `cdi-advws` pin themselves to a specific node
   (`fastapi-dynamiq` → `kube-dev`, `ai-hub-phalcon-php` → `kube-node3`) — unclear if
   that's a hard requirement (e.g. node-local storage) or just how those two happened to
   be scheduled. None of the new manifests set `nodeAffinity`; add it if the cluster needs
   it for scheduling or PVC binding to work in `ai-hub-advws`.
6. **Check whether cross-namespace traffic is allowed.** operations-advws (`cdi-advws`
   namespace) needs to reach `ai-gateway` (`ai-hub-advws` namespace) — different
   namespaces now, so this is cross-namespace Service DNS
   (`http://ai-gateway.ai-hub-advws.svc.cluster.local:8080`, not the bare `ai-gateway`
   name a same-namespace caller could use) **and**, if this multi-tenant cluster enforces
   any default-deny NetworkPolicy between the ~80 namespaces it hosts, an explicit
   NetworkPolicy allowing `cdi-advws` → `ai-hub-advws` on port 8080 may be required. This
   read-only token can't list NetworkPolicies to check either way — infra needs to confirm.
7. **Build + push the three images**, then `kubectl apply -k deploy/k8s/base/` (or
   `-f deploy/k8s/base/` if that matches the team's convention better — operations-advws
   itself doesn't use kustomize).
8. **Run `deploy/k8s/bootstrap-db.sh`** once the pods are up (one-time; safe to re-run).
9. **Verify**: `kubectl -n ai-hub-advws get pods` all Running, `/healthz` on each service,
   log into the web console via its NodePort through fake-sso, recreate a company →
   connection (OpenRouter) → agent → deployment → API key (already proven end-to-end on
   localhost earlier in this project), then update operations-advws's production `.env`
   (`DYNAMIQ_GATEWAY_URL=http://ai-gateway.ai-hub-advws.svc.cluster.local:8080`) and
   redeploy it to complete the original goal — a real chat message through
   `operations.advws.com` getting a real OpenRouter response.

## Known gaps, called out rather than guessed at

- No real SSO exists yet (PRD open question) — `fake-sso-deployment.yaml` is a clearly
  labeled temporary stand-in; swap it out the moment real SSO integration exists.
- No HA (single-replica Postgres/Redis, no CloudNativePG/Sentinel), no Ingress/TLS/WAF, no
  NetworkPolicy, no backup — all explicit scope-downs from PRD §11 for this first pass, not
  oversights.
- `redis-deployment.yaml` is committed and listed in `kustomization.yaml`, but isn't
  actually running in the cluster (`kubectl -n ai-hub-advws get pods` shows no `redis-...`
  pod as of this writing) — a pre-existing drift between git and the live cluster, found
  while preparing the MinIO follow-up below. Not fixed here (out of scope for that task);
  quota/rate-limit code already fails open without Redis, so nothing is broken by its
  absence, but it should be applied at some point so that code path actually works as
  designed.
- `ai-worker` (the async indexing queue for Knowledge Bases) is still not deployed —
  `kb_indexer.py` runs synchronously today, not through ARQ, so nothing needs it yet.
  **MinIO itself is now prepared** (manifests below) — see "Follow-up: deploy MinIO".

## Follow-up: deploy MinIO (KB storage — manifests ready, not yet applied)

**Superseded — see "Follow-up: remove MinIO..." below.** MinIO was applied per this
section, but then dropped again (license/cost concern) in favor of a plain PVC. Left here
only as history of why `ai-config`/`ai-runtime-deployment` once looked the way they did;
follow the newer section instead, not this one.

Knowledge Bases' OKF file storage (`ai/app/core/storage.py`) needs MinIO, which this first
rollout deliberately deferred. The feature itself (OKF import/export, indexing, vector +
Thai-aware hybrid search) is now built and tested locally — this is the one piece needed to
make it testable/usable against this cluster.

**Commits for this follow-up:**
- `14a4bc5` — the k8s manifest changes themselves (this section's files).
- `d05ed6b` — the Thai hybrid-search KB feature these manifests unblock (context only, no
  cluster-facing changes).

Prepared from the same read-only dev token
used for the rest of this document — confirmed via `kubectl -n ai-hub-advws auth can-i
create secrets/deployments/persistentvolumeclaims/configmaps` all returning `no`, so
applying this needs the same write access as the original rollout (see point 1 above).

**Files ready**: `deploy/k8s/base/minio-deployment.yaml` (new — Deployment + PVC + Service,
same shape as `redis-deployment.yaml`/`postgres-statefulset.yaml`), `ai-config.yaml`
(`MINIO_ENDPOINT`/`MINIO_BUCKET`/`MINIO_SECURE` added), `ai-runtime-deployment.yaml`
(`MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` added via `secretKeyRef` — not added to
`ai-gateway-deployment.yaml`, since KB endpoints live only in ai-runtime's internal API),
`kustomization.yaml` (lists the new file), `secrets.example.yaml` (documents the new
`dynamiq-minio-credentials` Secret).

**Steps once write access is available:**
1. `kubectl -n ai-hub-advws create secret generic dynamiq-minio-credentials --from-literal=root_user=minioadmin --from-literal=root_password="$(openssl rand -base64 24)"`
2. `kubectl apply -f deploy/k8s/base/minio-deployment.yaml`
3. `kubectl apply -f deploy/k8s/base/ai-config.yaml`
4. `kubectl apply -f deploy/k8s/base/ai-runtime-deployment.yaml` (rolls `ai-runtime` to pick
   up the two new env vars)
5. `kubectl -n ai-hub-advws get pods -w` until `minio-...` and the new `ai-runtime-...` pod
   are both `Running`/`Ready`.
6. Sanity check: `kubectl -n ai-hub-advws exec deploy/ai-runtime -- python -c "from app.core.storage import get_minio_client; print(get_minio_client().list_buckets())"`
   — the `ai-console` bucket auto-creates on first connection, no manual step needed.

**Deliberately not** a blanket `kubectl apply -k deploy/k8s/base/` — given the Redis drift
found above, this applies only the 3 files this follow-up actually changes, leaving
Postgres/console-api/ai-gateway/web/fake-sso untouched.

## Follow-up: remove MinIO, switch Knowledge Base storage to a local PVC, deploy ai-worker

**Why**: MinIO's license/cost was a concern, and nothing here needed an actual S3 API —
just "put/get/delete a blob by key". Knowledge Bases' OKF files now live as plain files
on a PVC mounted into `ai-runtime` (and `ai-worker`, see below), via
`ai/app/core/storage.py` + `KB_STORAGE_ROOT`.

Separately (found while testing this on the cluster): KB document import
(`enqueueKbDocumentIndexing` in `ai/app/main_runtime.py`) already enqueues indexing
through ARQ/Redis, but **no `ai-worker` deployment has ever existed on this cluster** —
so a queued import previously sat at `status="queued"` forever, nothing ever consumed the
queue. This follow-up adds that deployment too.

**What's in this follow-up**: `ai/app/core/storage.py` rewritten for plain file I/O (no
more `minio` package dependency — removed from `ai/pyproject.toml`/`uv.lock`);
`deploy/k8s/base/ai-config.yaml` (`MINIO_*` keys removed, `KB_STORAGE_ROOT` added);
`deploy/k8s/base/ai-runtime-deployment.yaml` (new `kb-storage` PVC + volume mount, old
`MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` env removed); `deploy/k8s/base/minio-deployment.yaml`
deleted; `deploy/k8s/base/ai-worker-deployment.yaml` (new — the ARQ consumer, shares the
`kb-storage` PVC, no Service/ports since it's not an HTTP server);
`deploy/k8s/base/secrets.example.yaml` (`dynamiq-minio-credentials` entry removed);
`Jenkinsfile` (now also `set image`s `deployment/ai-worker` on every build).

**Important**: the existing Jenkins job only runs `kubectl set image` on deployments that
*already exist* — it never applies manifest/config changes. Every step below needs to be
run manually, once, by someone with write access, before the next Jenkins-triggered
deploy can "just work" for `ai-worker`/the new PVC/the new ConfigMap key.

**Steps once write access is available** (run in this order):
1. `kubectl -n ai-hub-advws apply -f deploy/k8s/base/ai-config.yaml`
2. `kubectl -n ai-hub-advws apply -f deploy/k8s/base/ai-runtime-deployment.yaml` — pod
   spec changed (new volume mount, removed env), so this rolls `ai-runtime` automatically;
   no separate `rollout restart` needed.
3. `kubectl -n ai-hub-advws apply -f deploy/k8s/base/ai-worker-deployment.yaml` — creates
   `ai-worker` for the first time.
4. Remove the now-orphaned MinIO resources (kustomize doesn't prune on its own):
   ```
   kubectl -n ai-hub-advws delete deployment minio
   kubectl -n ai-hub-advws delete service minio
   kubectl -n ai-hub-advws delete pvc minio-data
   kubectl -n ai-hub-advws delete secret dynamiq-minio-credentials
   ```
5. `kubectl -n ai-hub-advws get pods` — confirm an `ai-worker-...` pod is `Running`/`Ready`
   and no `minio-...` pod remains.

**Note on the `kb-storage` PVC**: `ReadWriteOnce`, mounted by both `ai-runtime` and
`ai-worker`. Fine on this cluster's current single-node topology (everything schedules
onto `kube-dev` today) — if a second node is ever added, pin both Deployments to the same
node (matching point 5's node-affinity question from the original handoff above), or this
PVC will fail to attach to the second pod with a multi-attach error.
