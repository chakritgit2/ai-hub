<?php

declare(strict_types=1);

namespace ConsoleApi\Services;

use Phalcon\Db\Adapter\Pdo\AbstractPdo;
use Throwable;

/**
 * Writes `console.audit_logs` rows (PRD §8.1/§8.2). Used by AuditMiddleware (one row per
 * successful mutating request) and by AuthService (PRD §12's "company skipped + audit" —
 * an SSO login claiming a company ref with no matching `console.companies.external_ref`).
 *
 * A logging failure never breaks the request it's auditing — see log()'s catch.
 */
class AuditLogger
{
    public function __construct(private readonly AbstractPdo $db)
    {
    }

    /**
     * @param array<string, mixed>|null $diff
     */
    public function log(
        ?string $companyId,
        ?string $userId,
        string $action,
        string $entity,
        ?string $entityId,
        ?array $diff = null
    ): void {
        try {
            $this->insert($companyId, $userId, $action, $entity, $entityId, $diff);
        } catch (Throwable $exception) {
            error_log('AuditLogger: failed to write audit_logs row: ' . $exception->getMessage());
        }
    }

    /**
     * @param array<string, mixed>|null $diff
     */
    private function insert(
        ?string $companyId,
        ?string $userId,
        string $action,
        string $entity,
        ?string $entityId,
        ?array $diff
    ): void {
        $params = [
            'company_id' => $companyId,
            'user_id' => $userId,
            'action' => $action,
            'entity' => $entity,
            'entity_id' => $entityId,
            'diff' => $diff !== null ? json_encode($diff) : null,
        ];

        $sql = 'INSERT INTO console.audit_logs (company_id, user_id, action, entity, entity_id, diff)
                VALUES (:company_id, :user_id, :action, :entity, :entity_id, :diff::jsonb)';

        // audit_logs' RLS policy (db/migrations/post/002_rls_policies.sql) only requires
        // app.company_id when $companyId is set — a platform-level row (company_id NULL,
        // e.g. an unregistered SSO company ref) can insert with no transaction/company
        // context at all.
        if ($companyId === null) {
            $this->db->execute($sql, $params);

            return;
        }

        $this->db->begin();

        try {
            $context = new CompanyContext();
            $context->setCompanyId($companyId);
            $context->applyToConnection($this->db);

            $this->db->execute($sql, $params);

            $this->db->commit();
        } catch (Throwable $exception) {
            $this->db->rollback();

            throw $exception;
        }
    }
}
