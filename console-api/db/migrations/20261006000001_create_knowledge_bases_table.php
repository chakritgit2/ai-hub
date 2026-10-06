<?php

declare(strict_types=1);

use Phinx\Migration\AbstractMigration;

final class CreateKnowledgeBasesTable extends AbstractMigration
{
    public function up(): void
    {
        $this->execute(<<<SQL
            CREATE TABLE console.knowledge_bases (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                company_id uuid NOT NULL,
                name varchar(255) NOT NULL,
                embedder_connection_id uuid NOT NULL REFERENCES console.connections(id),
                chunk_size int NOT NULL DEFAULT 800,
                chunk_overlap int NOT NULL DEFAULT 100,
                retrieval_mode varchar(16) NOT NULL DEFAULT 'vector',
                alpha numeric(3,2) NOT NULL DEFAULT 0.60,
                okf_field_map jsonb NOT NULL DEFAULT '{}',
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT knowledge_bases_company_id_name_unique UNIQUE (company_id, name),
                CONSTRAINT knowledge_bases_retrieval_mode_check CHECK (retrieval_mode IN ('vector', 'hybrid')),
                CONSTRAINT knowledge_bases_chunk_size_check CHECK (chunk_size > 0),
                CONSTRAINT knowledge_bases_chunk_overlap_check CHECK (chunk_overlap >= 0),
                CONSTRAINT knowledge_bases_alpha_check CHECK (alpha >= 0 AND alpha <= 1)
            )
        SQL);

        $this->execute('CREATE INDEX knowledge_bases_company_id_idx ON console.knowledge_bases (company_id)');
    }

    public function down(): void
    {
        $this->execute('DROP TABLE IF EXISTS console.knowledge_bases');
    }
}
