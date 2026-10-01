<?php

declare(strict_types=1);

use Phinx\Migration\AbstractMigration;

final class CreateApiKeyScopesTable extends AbstractMigration
{
    public function up(): void
    {
        // company_id is redundant with api_keys.company_id/deployments.company_id (always
        // equal - enforced at the application layer in ApiKeysController, not by a DB
        // constraint, same as every cross-reference in this codebase) but is required
        // here anyway: every RLS-protected table in this schema scopes by its own
        // company_id column directly (db/migrations/post/002_rls_policies.sql's
        // `current_setting('app.company_id', true)` pattern), not by walking a join.
        $this->execute(<<<SQL
            CREATE TABLE console.api_key_scopes (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                company_id uuid NOT NULL,
                api_key_id uuid NOT NULL REFERENCES console.api_keys (id),
                deployment_id uuid NOT NULL REFERENCES console.deployments (id),
                created_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT api_key_scopes_api_key_id_deployment_id_unique UNIQUE (api_key_id, deployment_id)
            )
        SQL);

        $this->execute('CREATE INDEX api_key_scopes_company_id_idx ON console.api_key_scopes (company_id)');
        $this->execute('CREATE INDEX api_key_scopes_api_key_id_idx ON console.api_key_scopes (api_key_id)');
        $this->execute('CREATE INDEX api_key_scopes_deployment_id_idx ON console.api_key_scopes (deployment_id)');
    }

    public function down(): void
    {
        $this->execute('DROP TABLE IF EXISTS console.api_key_scopes');
    }
}
