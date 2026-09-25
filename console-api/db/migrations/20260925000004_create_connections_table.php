<?php

declare(strict_types=1);

use Phinx\Migration\AbstractMigration;

final class CreateConnectionsTable extends AbstractMigration
{
    public function up(): void
    {
        $this->execute(<<<SQL
            CREATE TABLE console.connections (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                company_id uuid NOT NULL,
                name varchar(255) NOT NULL,
                type varchar(255) NOT NULL,
                api_base varchar(1024),
                masked_hint varchar(255),
                max_concurrency integer NOT NULL DEFAULT 4,
                meta jsonb NOT NULL DEFAULT '{}',
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT connections_company_id_name_unique UNIQUE (company_id, name)
            )
        SQL);

        $this->execute('CREATE INDEX connections_company_id_idx ON console.connections (company_id)');
    }

    public function down(): void
    {
        $this->execute('DROP TABLE IF EXISTS console.connections');
    }
}
