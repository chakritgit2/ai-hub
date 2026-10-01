<?php

declare(strict_types=1);

use Phinx\Migration\AbstractMigration;

final class CreateAuditLogsTable extends AbstractMigration
{
    public function up(): void
    {
        $this->execute(<<<SQL
            CREATE TABLE console.audit_logs (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                company_id uuid,
                user_id uuid,
                action varchar(128) NOT NULL,
                entity varchar(64) NOT NULL,
                entity_id varchar(128),
                diff jsonb,
                created_at timestamptz NOT NULL DEFAULT now()
            )
        SQL);

        // company_id is nullable for platform-level events that have none — e.g. an SSO
        // login claiming a company ref with no matching console.companies.external_ref
        // (PRD §12 "company skipped + audit"). RLS in db/migrations/post/002_rls_policies.sql
        // makes those rows visible only to console_platform, which matches who actually
        // owns onboarding a new company (PRD §7.2).
        $this->execute('CREATE INDEX audit_logs_company_id_idx ON console.audit_logs (company_id)');
        $this->execute('CREATE INDEX audit_logs_created_at_idx ON console.audit_logs (created_at)');
    }

    public function down(): void
    {
        $this->execute('DROP TABLE IF EXISTS console.audit_logs');
    }
}
