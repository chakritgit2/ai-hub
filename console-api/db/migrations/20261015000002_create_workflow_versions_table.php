<?php

declare(strict_types=1);

use Phinx\Migration\AbstractMigration;

final class CreateWorkflowVersionsTable extends AbstractMigration
{
    public function up(): void
    {
        $this->execute(<<<SQL
            CREATE TABLE console.workflow_versions (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                company_id uuid NOT NULL,
                workflow_id uuid NOT NULL REFERENCES console.workflows (id),
                version_no integer NOT NULL,
                spec jsonb NOT NULL,
                spec_version varchar(32) NOT NULL,
                compiled_definition jsonb,
                compiler_version varchar(32),
                dynamiq_version varchar(32),
                is_published boolean NOT NULL DEFAULT false,
                published_by uuid,
                published_at timestamptz,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT workflow_versions_workflow_id_version_no_unique UNIQUE (workflow_id, version_no)
            )
        SQL);

        $this->execute('CREATE INDEX workflow_versions_company_id_idx ON console.workflow_versions (company_id)');
        $this->execute('CREATE INDEX workflow_versions_workflow_id_idx ON console.workflow_versions (workflow_id)');
    }

    public function down(): void
    {
        $this->execute('DROP TABLE IF EXISTS console.workflow_versions');
    }
}
