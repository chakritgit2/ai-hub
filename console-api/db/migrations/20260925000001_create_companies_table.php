<?php

declare(strict_types=1);

use Phinx\Migration\AbstractMigration;

final class CreateCompaniesTable extends AbstractMigration
{
    public function up(): void
    {
        // pgcrypto provides gen_random_uuid(); not created by db/migrations/pre/ (which
        // only creates the `vector` extension), so every table needing it creates it here.
        $this->execute('CREATE EXTENSION IF NOT EXISTS pgcrypto');

        $this->execute(<<<SQL
            CREATE TABLE console.companies (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                code varchar(255) NOT NULL,
                name varchar(255) NOT NULL,
                external_ref varchar(255),
                status varchar(32) NOT NULL DEFAULT 'active',
                monthly_budget_usd numeric(12, 2),
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT companies_code_unique UNIQUE (code)
            )
        SQL);
    }

    public function down(): void
    {
        $this->execute('DROP TABLE IF EXISTS console.companies');
    }
}
