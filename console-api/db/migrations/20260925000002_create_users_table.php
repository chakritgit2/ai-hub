<?php

declare(strict_types=1);

use Phinx\Migration\AbstractMigration;

final class CreateUsersTable extends AbstractMigration
{
    public function up(): void
    {
        $this->execute(<<<SQL
            CREATE TABLE console.users (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                external_sub varchar(255) NOT NULL,
                email varchar(255) NOT NULL,
                is_platform_admin boolean NOT NULL DEFAULT false,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT users_external_sub_unique UNIQUE (external_sub)
            )
        SQL);
    }

    public function down(): void
    {
        $this->execute('DROP TABLE IF EXISTS console.users');
    }
}
