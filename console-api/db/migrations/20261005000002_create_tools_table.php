<?php

declare(strict_types=1);

use Phinx\Migration\AbstractMigration;

final class CreateToolsTable extends AbstractMigration
{
    public function up(): void
    {
        $this->execute(<<<SQL
            CREATE TABLE console.tools (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                company_id uuid NOT NULL,
                name varchar(255) NOT NULL,
                kind varchar(32) NOT NULL,
                access_level varchar(16) NOT NULL DEFAULT 'read',
                auth_mode varchar(16) NOT NULL DEFAULT 'service',
                audience varchar(255),
                config jsonb NOT NULL DEFAULT '{}',
                enabled boolean NOT NULL DEFAULT true,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT tools_company_id_name_unique UNIQUE (company_id, name),
                CONSTRAINT tools_kind_check CHECK (kind IN ('http', 'builtin', 'python')),
                CONSTRAINT tools_access_level_check CHECK (access_level IN ('read', 'write')),
                CONSTRAINT tools_auth_mode_check CHECK (auth_mode IN ('service', 'delegated'))
            )
        SQL);

        $this->execute('CREATE INDEX tools_company_id_idx ON console.tools (company_id)');
    }

    public function down(): void
    {
        $this->execute('DROP TABLE IF EXISTS console.tools');
    }
}
