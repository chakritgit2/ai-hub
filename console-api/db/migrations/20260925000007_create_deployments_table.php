<?php

declare(strict_types=1);

use Phinx\Migration\AbstractMigration;

final class CreateDeploymentsTable extends AbstractMigration
{
    public function up(): void
    {
        $this->execute(<<<SQL
            CREATE TABLE console.deployments (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                company_id uuid NOT NULL,
                slug varchar(255) NOT NULL,
                environment varchar(32) NOT NULL DEFAULT 'production',
                agent_version_id uuid NOT NULL REFERENCES console.agent_versions (id),
                rate_limit_per_min integer NOT NULL DEFAULT 60,
                daily_token_limit bigint,
                daily_cost_limit_usd numeric(12, 2),
                allowed_origins jsonb NOT NULL DEFAULT '[]',
                guardrail_overrides jsonb NOT NULL DEFAULT '{}',
                output_mode varchar(32) NOT NULL DEFAULT 'stream',
                allow_write_tools boolean NOT NULL DEFAULT false,
                conversation_ttl_days integer NOT NULL DEFAULT 30,
                config_version integer NOT NULL DEFAULT 1,
                enabled boolean NOT NULL DEFAULT true,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT deployments_slug_unique UNIQUE (slug)
            )
        SQL);

        $this->execute('CREATE INDEX deployments_company_id_idx ON console.deployments (company_id)');
        $this->execute('CREATE INDEX deployments_agent_version_id_idx ON console.deployments (agent_version_id)');
    }

    public function down(): void
    {
        $this->execute('DROP TABLE IF EXISTS console.deployments');
    }
}
