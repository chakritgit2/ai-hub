<?php

declare(strict_types=1);

use Phinx\Migration\AbstractMigration;

/**
 * Backfills a single default tenant and points every existing company at it, then makes
 * tenant_id NOT NULL - every company must belong to exactly one tenant going forward.
 * Seeded as 'advws'/ADVWS (the org this console is deployed for, per its own user base);
 * rename later with a plain UPDATE if that's wrong - no rename endpoint exists yet since
 * TenantsController mirrors CompaniesController's create/suspend/activate-only shape.
 */
final class AddTenantIdToCompanies extends AbstractMigration
{
    public function up(): void
    {
        $this->execute(<<<SQL
            ALTER TABLE console.companies ADD COLUMN tenant_id uuid REFERENCES console.tenants (id)
        SQL);

        $this->execute(<<<SQL
            INSERT INTO console.tenants (code, name) VALUES ('advws', 'ADVWS')
        SQL);

        $this->execute(<<<SQL
            UPDATE console.companies
            SET tenant_id = (SELECT id FROM console.tenants WHERE code = 'advws')
            WHERE tenant_id IS NULL
        SQL);

        $this->execute('ALTER TABLE console.companies ALTER COLUMN tenant_id SET NOT NULL');
        $this->execute('CREATE INDEX companies_tenant_id_idx ON console.companies (tenant_id)');
    }

    public function down(): void
    {
        $this->execute('ALTER TABLE console.companies DROP COLUMN IF EXISTS tenant_id');
        // Undo exactly what up() inserted, so a migrate/rollback/migrate cycle doesn't hit
        // tenants_code_unique on the second up() (the "create tenants table" migration's own
        // down() - which drops the whole table - only runs if that migration is also rolled
        // back, which doesn't happen when just this one migration is reverted).
        $this->execute("DELETE FROM console.tenants WHERE code = 'advws'");
    }
}
