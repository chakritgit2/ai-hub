# db/ — shared schema owner (PRD §8.1)

`db/` owns everything that is *not* a single service's own tables: schemas, DB roles, the
`v1_*` compatibility views Python reads from `console`, `FORCE ROW LEVEL SECURITY` policies, and
`SECURITY DEFINER` functions like `resolve_api_key`.

PRD §8.1 describes this as "one migration set, run before Phinx/Alembic" — but the views/RLS/
functions here reference tables that Phinx and Alembic create, so a literal single "run first"
step is impossible. This skeleton splits `db/migrations/` into two stages run around the
per-service migrators:

```
1. db/migrations/pre/*.sql      (schemas, extensions, role placeholders)
2. console-api  → vendor/bin/phinx migrate    (creates `console` tables)
   ai            → alembic upgrade head        (creates `runtime` and `logs` tables)
3. db/migrations/post/*.sql     (v1_* views, RLS policies, resolve_api_key())
```

Plain numbered `.sql` files, applied with `psql -f`, not a third migration framework — the
project doesn't need one yet, and `db/pre` is idempotent (`IF NOT EXISTS` guards) so it is safe
to re-run.

`db/contract_tests/` will hold CI tests that log in as each DB role and assert that (a) required
columns are selectable and (b) other companies' rows are invisible. Tooling (pgTAP vs.
pytest+psycopg vs. PHPUnit) is not yet decided — see the placeholder README there.
