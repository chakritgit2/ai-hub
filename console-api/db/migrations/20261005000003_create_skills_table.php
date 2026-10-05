<?php

declare(strict_types=1);

use Phinx\Migration\AbstractMigration;

final class CreateSkillsTable extends AbstractMigration
{
    public function up(): void
    {
        $this->execute(<<<SQL
            CREATE TABLE console.skills (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                company_id uuid NOT NULL,
                name varchar(255) NOT NULL,
                description varchar(1024),
                status varchar(32) NOT NULL DEFAULT 'draft',
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT skills_company_id_name_unique UNIQUE (company_id, name)
            )
        SQL);

        $this->execute('CREATE INDEX skills_company_id_idx ON console.skills (company_id)');
    }

    public function down(): void
    {
        $this->execute('DROP TABLE IF EXISTS console.skills');
    }
}
