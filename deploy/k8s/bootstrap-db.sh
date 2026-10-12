#!/usr/bin/env bash
# DB bootstrap/migration runner for the ai-hub-advws deployment. Run on EVERY Jenkins deploy
# (Jenkinsfile stage "Run DB migrations", AFTER "Deploy to Kubernetes"), and safe to run by
# hand. Fully idempotent: step 1 uses IF NOT EXISTS; steps 2-3 (Phinx/Alembic) track applied
# versions and no-op when nothing is new; step 4's post/*.sql use DROP POLICY IF EXISTS /
# CREATE OR REPLACE.
#
# Why AFTER the rollout, not before: steps 2-3 run Phinx/Alembic inside the console-api and
# ai-runtime pods, i.e. with the migration files baked into whatever image those pods run.
# Before the rollout that is still the OLD image, so new migrations would never be applied.
#
# Order matters: schemas/roles -> Phinx (console tables, owned by db_owner) ->
# Alembic (runtime/logs tables, owned by ai_app) -> post/*.sql (views/RLS/functions that
# reference tables from both of the previous two steps).
#
# Changes vs. the previous version (same steps, same order, same SQL):
#   - psql runs with ON_ERROR_STOP=1: a failing statement now fails the script (and the
#     Jenkins build) instead of being printed and ignored.
#   - No files containing passwords: the rendered 001 SQL and every post file are streamed
#     to psql over stdin (no /tmp file on the Jenkins agent, no kubectl cp into the pod).
#   - console-api / ai-runtime are reached via deploy/<name>, which picks a Ready pod of the
#     current ReplicaSet instead of possibly a still-terminating old pod.
#   - db_owner's password reaches Phinx via stdin, not the command line; Alembic uses the
#     ai_app DATABASE_URL already present in the ai-runtime pod (same value as before).
set -euo pipefail

NAMESPACE="${NAMESPACE:-ai-hub-advws}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PSQL=(psql -X -v ON_ERROR_STOP=1 -U postgres -d ai_console)

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
die() { printf '[%s] ERROR: %s\n' "$(date '+%H:%M:%S')" "$*" >&2; exit 1; }

secret() {
  local v
  v="$(kubectl -n "$NAMESPACE" get secret dynamiq-db-credentials -o jsonpath="{.data.$1}" | base64 -d)"
  [[ -n "$v" ]] || die "Key '$1' missing or empty in Secret dynamiq-db-credentials"
  printf '%s' "$v"
}

# Real values come from the dynamiq-db-credentials Secret — never hardcode them here.
DB_OWNER_PASSWORD="$(secret db_owner_password)"
CONSOLE_APP_PASSWORD="$(secret console_app_password)"
CONSOLE_PLATFORM_PASSWORD="$(secret console_platform_password)"
AI_APP_PASSWORD="$(secret ai_app_password)"
GATEWAY_APP_PASSWORD="$(secret gateway_app_password)"

POSTGRES_POD="$(kubectl -n "$NAMESPACE" get pod -l app=dynamiq-postgres \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')"
[[ -n "$POSTGRES_POD" ]] || die "No running Postgres pod (label app=dynamiq-postgres)"

# Make sure the pods we exec into run the newly deployed image (no-op if already rolled out)
kubectl -n "$NAMESPACE" rollout status deployment/console-api --timeout=300s
kubectl -n "$NAMESPACE" rollout status deployment/ai-runtime  --timeout=300s

pg() { kubectl -n "$NAMESPACE" exec -i "$POSTGRES_POD" -c postgres -- "${PSQL[@]}"; }

log "==> 1/4: schemas, vector extension, roles (db/migrations/pre/001_schemas_roles_extensions.sql)"
PRE_FILE="$REPO_ROOT/db/migrations/pre/001_schemas_roles_extensions.sql"
[[ -f "$PRE_FILE" ]] || die "Not found: $PRE_FILE"
# The first replacement gives every role db_owner's password; the per-role expressions then
# give each of the other four roles its own password (same logic as before, now streamed).
sed -e "s/'changeme_local_dev_only'/'${DB_OWNER_PASSWORD}'/1" "$PRE_FILE" \
  | sed \
      -e "0,/console_app LOGIN PASSWORD '[^']*'/s//console_app LOGIN PASSWORD '${CONSOLE_APP_PASSWORD}'/" \
      -e "0,/console_platform LOGIN PASSWORD '[^']*'/s//console_platform LOGIN PASSWORD '${CONSOLE_PLATFORM_PASSWORD}'/" \
      -e "0,/ai_app LOGIN PASSWORD '[^']*'/s//ai_app LOGIN PASSWORD '${AI_APP_PASSWORD}'/" \
      -e "0,/gateway_app LOGIN PASSWORD '[^']*'/s//gateway_app LOGIN PASSWORD '${GATEWAY_APP_PASSWORD}'/" \
  | pg

log "==> 2/4: Phinx migrate (console schema, connects as db_owner)"
# shellcheck disable=SC2016  # expanded by the shell inside the pod
printf '%s\n' "$DB_OWNER_PASSWORD" | kubectl -n "$NAMESPACE" exec -i deploy/console-api -c console-api -- sh -c '
  IFS= read -r DB_OWNER_PASSWORD
  export DB_OWNER_PASSWORD DB_HOST=postgres DB_NAME=ai_console DB_SCHEMA=console DB_OWNER_USER=db_owner
  exec vendor/bin/phinx migrate -c db/migrations/phinx.php'

log "==> 3/4: Alembic upgrade head (runtime+logs schemas, connects as ai_app)"
# ai-runtime's own DATABASE_URL is the ai_app DSN from the same Secret.
kubectl -n "$NAMESPACE" exec deploy/ai-runtime -c ai -- uv run alembic upgrade head

log "==> 4/4: post/*.sql (views, RLS, resolve_* functions) — numeric order"
shopt -s nullglob
post_files=("$REPO_ROOT"/db/migrations/post/*.sql)
[[ ${#post_files[@]} -gt 0 ]] || die "No files in $REPO_ROOT/db/migrations/post/"
for f in "${post_files[@]}"; do
  log "    - $(basename "$f")"
  pg < "$f"
done

log "==> Done. Verify: kubectl -n $NAMESPACE exec $POSTGRES_POD -- psql -U postgres -d ai_console -c '\\dn' (expect console, runtime, logs)"