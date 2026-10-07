#!/usr/bin/env bash
# One-time production DB bootstrap for the ai-hub-advws deployment (see the session plan).
# Run manually after `kubectl apply -k deploy/k8s/base` has created the postgres/console-api/
# ai-runtime pods, with kubectl pointed at ai-hub-advws-cluster. Idempotent for step 1
# (pre/001's role creation uses IF NOT EXISTS); steps 2-4 are plain migrations, safe to
# re-run once applied (Phinx/Alembic track their own version tables).
#
# Order matters: schemas/roles -> Phinx (console tables, owned by db_owner) ->
# Alembic (runtime/logs tables, owned by ai_app) -> post/*.sql (views/RLS/functions that
# reference tables from both of the previous two steps).
set -euo pipefail

NAMESPACE=ai-hub-advws
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Real values come from the dynamiq-db-credentials Secret created per
# deploy/k8s/base/secrets.example.yaml — never hardcode them here.
DB_OWNER_PASSWORD=$(kubectl -n "$NAMESPACE" get secret dynamiq-db-credentials -o jsonpath='{.data.db_owner_password}' | base64 -d)
CONSOLE_APP_PASSWORD=$(kubectl -n "$NAMESPACE" get secret dynamiq-db-credentials -o jsonpath='{.data.console_app_password}' | base64 -d)
CONSOLE_PLATFORM_PASSWORD=$(kubectl -n "$NAMESPACE" get secret dynamiq-db-credentials -o jsonpath='{.data.console_platform_password}' | base64 -d)
AI_APP_PASSWORD=$(kubectl -n "$NAMESPACE" get secret dynamiq-db-credentials -o jsonpath='{.data.ai_app_password}' | base64 -d)
GATEWAY_APP_PASSWORD=$(kubectl -n "$NAMESPACE" get secret dynamiq-db-credentials -o jsonpath='{.data.gateway_app_password}' | base64 -d)

POSTGRES_POD=$(kubectl -n "$NAMESPACE" get pod -l app=dynamiq-postgres -o jsonpath='{.items[0].metadata.name}')
CONSOLE_API_POD=$(kubectl -n "$NAMESPACE" get pod -l app=console-api -o jsonpath='{.items[0].metadata.name}')
AI_RUNTIME_POD=$(kubectl -n "$NAMESPACE" get pod -l app=ai-runtime -o jsonpath='{.items[0].metadata.name}')

echo "==> 1/4: schemas, vector extension, roles (db/migrations/pre/001_schemas_roles_extensions.sql)"
sed \
  -e "s/'changeme_local_dev_only'/'${DB_OWNER_PASSWORD}'/1" \
  "$REPO_ROOT/db/migrations/pre/001_schemas_roles_extensions.sql" \
  > /tmp/001_schemas_roles_extensions.prod.sql
# The five CREATE ROLE statements each reuse the same literal password in the source file;
# patch the remaining four in place per-role so every role gets its own real password.
sed -i \
  -e "0,/console_app LOGIN PASSWORD '[^']*'/s//console_app LOGIN PASSWORD '${CONSOLE_APP_PASSWORD}'/" \
  -e "0,/console_platform LOGIN PASSWORD '[^']*'/s//console_platform LOGIN PASSWORD '${CONSOLE_PLATFORM_PASSWORD}'/" \
  -e "0,/ai_app LOGIN PASSWORD '[^']*'/s//ai_app LOGIN PASSWORD '${AI_APP_PASSWORD}'/" \
  -e "0,/gateway_app LOGIN PASSWORD '[^']*'/s//gateway_app LOGIN PASSWORD '${GATEWAY_APP_PASSWORD}'/" \
  /tmp/001_schemas_roles_extensions.prod.sql
kubectl -n "$NAMESPACE" cp /tmp/001_schemas_roles_extensions.prod.sql "$POSTGRES_POD":/tmp/001.sql
kubectl -n "$NAMESPACE" exec "$POSTGRES_POD" -- psql -U postgres -d ai_console -f /tmp/001.sql

echo "==> 2/4: Phinx migrate (console schema, connects as db_owner)"
kubectl -n "$NAMESPACE" exec "$CONSOLE_API_POD" -c console-api -- env \
  DB_HOST=postgres DB_NAME=ai_console DB_SCHEMA=console \
  DB_OWNER_USER=db_owner DB_OWNER_PASSWORD="$DB_OWNER_PASSWORD" \
  vendor/bin/phinx migrate -c db/migrations/phinx.php

echo "==> 3/4: Alembic upgrade head (runtime+logs schemas, connects as ai_app)"
kubectl -n "$NAMESPACE" exec "$AI_RUNTIME_POD" -- env \
  DATABASE_URL="postgresql+asyncpg://ai_app:${AI_APP_PASSWORD}@postgres:5432/ai_console" \
  uv run alembic upgrade head

echo "==> 4/4: post/*.sql (views, RLS, resolve_* functions) — numeric order"
for f in "$REPO_ROOT"/db/migrations/post/*.sql; do
  name=$(basename "$f")
  echo "    - $name"
  kubectl -n "$NAMESPACE" cp "$f" "$POSTGRES_POD":/tmp/"$name"
  kubectl -n "$NAMESPACE" exec "$POSTGRES_POD" -- psql -U postgres -d ai_console -f /tmp/"$name"
done

echo "==> Done. Verify: kubectl -n $NAMESPACE exec $POSTGRES_POD -- psql -U postgres -d ai_console -c '\dn' (expect console, runtime, logs)"
