<?php

declare(strict_types=1);

use Phinx\Migration\AbstractMigration;

final class CreateEgressAllowlistTable extends AbstractMigration
{
    public function up(): void
    {
        $this->execute(<<<SQL
            CREATE TABLE console.egress_allowlist (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                company_id uuid,
                host_pattern varchar(255) NOT NULL,
                port integer,
                allow_private_ip boolean NOT NULL DEFAULT false,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        SQL);

        // company_id nullable = platform-wide entry (PRD §8.1), applying to every
        // company's SafeHttpClient checks (ai/app/integrations/safe_http_client.py,
        // currently a stub) — not creatable through this console-api CRUD surface today
        // (every entry this API writes has a real company_id), only reserved for a
        // future platform_admin-seeded row.
        $this->execute('CREATE INDEX egress_allowlist_company_id_idx ON console.egress_allowlist (company_id)');
    }

    public function down(): void
    {
        $this->execute('DROP TABLE IF EXISTS console.egress_allowlist');
    }
}
