<?php

declare(strict_types=1);

use Phinx\Migration\AbstractMigration;

final class CreateSkillVersionsTable extends AbstractMigration
{
    public function up(): void
    {
        $this->execute(<<<SQL
            CREATE TABLE console.skill_versions (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                company_id uuid NOT NULL,
                skill_id uuid NOT NULL REFERENCES console.skills(id),
                version_no integer NOT NULL,
                content text NOT NULL,
                content_hash varchar(64) NOT NULL,
                has_scripts boolean NOT NULL DEFAULT false,
                is_published boolean NOT NULL DEFAULT false,
                published_by uuid,
                published_at timestamptz,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT skill_versions_skill_id_version_no_unique UNIQUE (skill_id, version_no),
                CONSTRAINT skill_versions_content_max_bytes CHECK (octet_length(content) <= 102400)
            )
        SQL);

        $this->execute('CREATE INDEX skill_versions_company_id_idx ON console.skill_versions (company_id)');
        $this->execute('CREATE INDEX skill_versions_skill_id_idx ON console.skill_versions (skill_id)');
    }

    public function down(): void
    {
        $this->execute('DROP TABLE IF EXISTS console.skill_versions');
    }
}
