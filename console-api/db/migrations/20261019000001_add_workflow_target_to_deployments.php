<?php

declare(strict_types=1);

use Phinx\Migration\AbstractMigration;

final class AddWorkflowTargetToDeployments extends AbstractMigration
{
    public function up(): void
    {
        $this->execute(<<<SQL
            ALTER TABLE console.deployments ALTER COLUMN agent_version_id DROP NOT NULL
        SQL);

        $this->execute(<<<SQL
            ALTER TABLE console.deployments
                ADD COLUMN workflow_version_id uuid REFERENCES console.workflow_versions (id)
        SQL);

        $this->execute(<<<SQL
            ALTER TABLE console.deployments
                ADD CONSTRAINT deployments_exactly_one_target CHECK (
                    (agent_version_id IS NOT NULL)::int + (workflow_version_id IS NOT NULL)::int = 1
                )
        SQL);

        $this->execute(
            'CREATE INDEX deployments_workflow_version_id_idx ON console.deployments (workflow_version_id)'
        );
    }

    public function down(): void
    {
        $this->execute('DROP INDEX IF EXISTS console.deployments_workflow_version_id_idx');
        $this->execute(
            'ALTER TABLE console.deployments DROP CONSTRAINT IF EXISTS deployments_exactly_one_target'
        );
        $this->execute('ALTER TABLE console.deployments DROP COLUMN IF EXISTS workflow_version_id');
        $this->execute('ALTER TABLE console.deployments ALTER COLUMN agent_version_id SET NOT NULL');
    }
}
