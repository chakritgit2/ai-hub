<?php

declare(strict_types=1);

use Phinx\Migration\AbstractMigration;

final class CreateAgentsTable extends AbstractMigration
{
    public function up(): void
    {
        $this->execute(<<<SQL
            CREATE TABLE console.agents (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                company_id uuid NOT NULL,
                name varchar(255) NOT NULL,
                description text,
                status varchar(32) NOT NULL DEFAULT 'draft',
                archived_at timestamptz,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT agents_company_id_name_unique UNIQUE (company_id, name)
            )
        SQL);

        $this->execute('CREATE INDEX agents_company_id_idx ON console.agents (company_id)');
    }

    public function down(): void
    {
        $this->execute('DROP TABLE IF EXISTS console.agents');
    }
}
