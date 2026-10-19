<?php

declare(strict_types=1);

use Phinx\Migration\AbstractMigration;

final class AddPromotedToIdToDeployments extends AbstractMigration
{
    public function up(): void
    {
        $this->execute(<<<SQL
            ALTER TABLE console.deployments
                ADD COLUMN promoted_to_id uuid REFERENCES console.deployments (id)
        SQL);

        $this->execute(
            'CREATE INDEX deployments_promoted_to_id_idx ON console.deployments (promoted_to_id)'
        );
    }

    public function down(): void
    {
        $this->execute('DROP INDEX IF EXISTS console.deployments_promoted_to_id_idx');
        $this->execute('ALTER TABLE console.deployments DROP COLUMN IF EXISTS promoted_to_id');
    }
}
