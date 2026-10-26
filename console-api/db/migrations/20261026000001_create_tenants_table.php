<?php

declare(strict_types=1);

use Phinx\Migration\AbstractMigration;

/**
 * The org-level grouping above console.companies - today there is exactly one tenant
 * (the org running this console), but the data model allows more later without touching
 * the existing company-scoped RLS, which stays keyed by company_id regardless of how many
 * tenants own those companies. Same shape/no-RLS reasoning as console.companies itself
 * (PRD §7.3: company-scoping applies to rows *belonging to* a company, not to the
 * companies/tenants tables themselves) - platform_admin-only via requirePlatformAdmin(),
 * see TenantsController.
 */
final class CreateTenantsTable extends AbstractMigration
{
    public function up(): void
    {
        $this->execute(<<<SQL
            CREATE TABLE console.tenants (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                code varchar(255) NOT NULL,
                name varchar(255) NOT NULL,
                status varchar(32) NOT NULL DEFAULT 'active',
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT tenants_code_unique UNIQUE (code)
            )
        SQL);
    }

    public function down(): void
    {
        $this->execute('DROP TABLE IF EXISTS console.tenants');
    }
}
