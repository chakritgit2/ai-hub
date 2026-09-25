<?php

declare(strict_types=1);

namespace ConsoleApi\Services;

use InvalidArgumentException;
use Phalcon\Db\Adapter\Pdo\AbstractPdo;
use RuntimeException;

/**
 * Holds the X-Company-Id header for the current request and applies it to a Postgres
 * connection as the RLS session variable that every FORCE ROW LEVEL SECURITY policy
 * checks (PRD §7.2, §7.3).
 *
 * The company is applied with `SET LOCAL app.company_id` — scoped to the *current
 * transaction only* and cleared automatically at COMMIT/ROLLBACK. This is required
 * because PgBouncer runs in transaction pooling mode and PHP-FPM reuses connections
 * across requests: a session-level SET would leak company A's context into a later
 * request that happens to reuse the same backend connection. Policies read it via
 * `current_setting('app.company_id', true)`, which is fail-closed (no rows visible
 * when unset).
 */
class CompanyContext
{
    private ?string $companyId = null;

    public function setCompanyId(?string $companyId): void
    {
        $this->companyId = $companyId;
    }

    public function getCompanyId(): ?string
    {
        return $this->companyId;
    }

    public function hasCompanyId(): bool
    {
        return $this->companyId !== null && $this->companyId !== '';
    }

    /**
     * Runs `SET LOCAL app.company_id = '<id>'` against $db.
     *
     * Must be called after $db->begin() and before any company-scoped query within
     * that same transaction. `SET LOCAL` does not support bound parameters, so the
     * value is validated against a conservative allowlist pattern and single quotes
     * are doubled defensively before being inlined.
     */
    public function applyToConnection(AbstractPdo $db): void
    {
        if (!$this->hasCompanyId()) {
            throw new RuntimeException('CompanyContext: no company_id bound for this request.');
        }

        if (!preg_match('/^[A-Za-z0-9_\-:.]{1,128}$/', $this->companyId)) {
            throw new InvalidArgumentException('CompanyContext: invalid company_id format.');
        }

        $escaped = str_replace("'", "''", $this->companyId);
        $db->execute(sprintf("SET LOCAL app.company_id = '%s'", $escaped));
    }
}
