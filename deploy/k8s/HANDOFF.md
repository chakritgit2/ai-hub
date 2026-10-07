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
- `ai-worker` (KB indexing) and MinIO are not deployed — the Knowledge Bases feature is out
  of scope for "get chat working" and isn't touched by the compile/publish/deploy/gateway
  path at all.
