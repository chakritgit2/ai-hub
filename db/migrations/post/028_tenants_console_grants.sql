-- Runs AFTER Phinx has created console.tenants (20261026000001). PRD-001 §8.1 (tenants
-- sit above companies, same "platform_admin manages it" reasoning as console.companies
-- itself).
--
-- Deliberately NO RLS here, same as console.companies/console.users (PRD §7.3:
-- company-scoping applies to rows *belonging to* a company, not to this table) -
-- TenantsController gates every method with requirePlatformAdmin(), not RLS. db_owner
-- already owns this table (Phinx connects as db_owner) - console_app/console_platform
-- both need full CRUD since TenantsController does INSERT/UPDATE through console_app,
-- same grant shape as 002_rls_policies.sql's original console.companies grant.

GRANT SELECT, INSERT, UPDATE, DELETE ON console.tenants TO console_app, console_platform;
