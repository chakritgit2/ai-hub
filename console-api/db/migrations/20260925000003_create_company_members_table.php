<?php

declare(strict_types=1);

use Phinx\Migration\AbstractMigration;

final class CreateCompanyMembersTable extends AbstractMigration
{
    public function up(): void
    {
        $this->execute(<<<SQL
            CREATE TABLE console.company_members (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                company_id uuid NOT NULL REFERENCES console.companies (id),
                user_id uuid NOT NULL REFERENCES console.users (id),
                role varchar(32) NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT company_members_role_check CHECK (role IN ('admin', 'developer', 'viewer')),
                CONSTRAINT company_members_company_id_user_id_unique UNIQUE (company_id, user_id)
            )
        SQL);

        $this->execute('CREATE INDEX company_members_company_id_idx ON console.company_members (company_id)');
    }

    public function down(): void
    {
        $this->execute('DROP TABLE IF EXISTS console.company_members');
    }
}
