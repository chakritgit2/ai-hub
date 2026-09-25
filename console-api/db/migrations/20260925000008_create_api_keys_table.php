<?php

declare(strict_types=1);

use Phinx\Migration\AbstractMigration;

final class CreateApiKeysTable extends AbstractMigration
{
    public function up(): void
    {
        $this->execute(<<<SQL
            CREATE TABLE console.api_keys (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                company_id uuid NOT NULL,
                name varchar(255) NOT NULL,
                key_hash varchar(255) NOT NULL,
                prefix varchar(32) NOT NULL,
                allowed_ips jsonb NOT NULL DEFAULT '[]',
                rate_limit_per_min integer,
                daily_cost_limit_usd numeric(12, 2),
                last_used_at timestamptz,
                created_by uuid,
                revoked_at timestamptz,
                created_at timestamptz NOT NULL DEFAULT now()
            )
        SQL);

        $this->execute('CREATE INDEX api_keys_company_id_idx ON console.api_keys (company_id)');
    }

    public function down(): void
    {
        $this->execute('DROP TABLE IF EXISTS console.api_keys');
    }
}
