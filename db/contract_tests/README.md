# db/contract_tests — placeholder

CI is supposed to log in as each DB role (`console_app`, `console_platform`, `ai_app`,
`gateway_app`) and verify:

1. The columns each role's queries depend on (e.g. Python's `v1_*` view reads) are still
   selectable after a schema change.
2. Row Level Security actually hides other companies' rows for that role.

Tooling is not yet chosen (candidates: pgTAP, pytest + psycopg, PHPUnit against the same DB) —
this file is a placeholder until a test framework is picked; no runnable tests exist here yet.
