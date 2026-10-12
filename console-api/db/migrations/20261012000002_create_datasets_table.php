<?php

declare(strict_types=1);

use Phinx\Migration\AbstractMigration;

final class CreateDatasetsTable extends AbstractMigration
{
    public function up(): void
    {
        $this->execute(<<<SQL
            CREATE TABLE console.datasets (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                company_id uuid NOT NULL,
                name varchar(255) NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT datasets_company_id_name_unique UNIQUE (company_id, name)
            )
        SQL);

        $this->execute('CREATE INDEX datasets_company_id_idx ON console.datasets (company_id)');

        $this->execute(<<<SQL
            CREATE TABLE console.dataset_items (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                company_id uuid NOT NULL,
                dataset_id uuid NOT NULL REFERENCES console.datasets(id) ON DELETE CASCADE,
                question text NOT NULL,
                expected_answer text,
                context text,
                tags jsonb NOT NULL DEFAULT '[]',
                created_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT dataset_items_question_not_blank CHECK (btrim(question) <> '')
            )
        SQL);

        $this->execute('CREATE INDEX dataset_items_company_id_idx ON console.dataset_items (company_id)');
        $this->execute('CREATE INDEX dataset_items_dataset_id_idx ON console.dataset_items (dataset_id)');
    }

    public function down(): void
    {
        $this->execute('DROP TABLE IF EXISTS console.dataset_items');
        $this->execute('DROP TABLE IF EXISTS console.datasets');
    }
}
