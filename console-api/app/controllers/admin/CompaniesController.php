<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Db\Enum;
use Phalcon\Http\Response;
use Throwable;

/**
 * /companies — platform_admin only (PRD §8.1 `companies` table). Unlike every other
 * controller here, these routes do not require X-Company-Id.
 *
 * `console.companies`/`console.users` have no RLS (PRD §7.3: company-scoping applies to
 * rows *belonging to* a company, not to the companies table itself) and `console_app`
 * already has a direct table grant, so most methods here query `$db` plainly - no
 * runInCompanyTransaction()/CompanyContext involved. `destroyCompanyDek`/
 * `listCompanyMembers` are the exception: they reach into RLS-protected tables
 * (runtime.company_keys, console.company_members) scoped to the *target* company from
 * the `{id}` path param, via runInTransactionAsCompany().
 */
class CompaniesController extends ControllerBase
{
    public function listCompanies(): Response
    {
        if (($error = $this->requirePlatformAdmin()) !== null) {
            return $error;
        }

        $db = $this->getDI()->getShared('db');
        $rows = $db->fetchAll(
            'SELECT id, code, name, external_ref, status, monthly_budget_usd, created_at, updated_at
             FROM console.companies ORDER BY created_at DESC',
            Enum::FETCH_ASSOC
        );

        return $this->jsonResponse(array_map([$this, 'formatCompany'], $rows));
    }

    public function createCompany(): Response
    {
        if (($error = $this->requirePlatformAdmin()) !== null) {
            return $error;
        }

        $body = $this->request->getJsonRawBody(true) ?? [];
        $code = $body['code'] ?? null;
        if (!is_string($code) || trim($code) === '') {
            return $this->jsonResponse(['error' => 'code_required'], 400);
        }
        $name = $body['name'] ?? null;
        if (!is_string($name) || trim($name) === '') {
            return $this->jsonResponse(['error' => 'name_required'], 400);
        }
        $externalRef = is_string($body['external_ref'] ?? null) ? $body['external_ref'] : null;
        $monthlyBudgetUsd = null;
        if (array_key_exists('monthly_budget_usd', $body) && $body['monthly_budget_usd'] !== null) {
            if (!is_numeric($body['monthly_budget_usd'])) {
                return $this->jsonResponse(['error' => 'invalid_monthly_budget_usd'], 400);
            }
            $monthlyBudgetUsd = $body['monthly_budget_usd'];
        }

        $db = $this->getDI()->getShared('db');
        try {
            $row = $db->fetchOne(
                'INSERT INTO console.companies (code, name, external_ref, monthly_budget_usd)
                 VALUES (:code, :name, :external_ref, :monthly_budget_usd)
                 RETURNING id, code, name, external_ref, status, monthly_budget_usd, created_at, updated_at',
                Enum::FETCH_ASSOC,
                ['code' => $code, 'name' => $name, 'external_ref' => $externalRef, 'monthly_budget_usd' => $monthlyBudgetUsd]
            );
        } catch (Throwable $exception) {
            if ($this->isUniqueViolation($exception)) {
                return $this->jsonResponse(['error' => 'code_already_exists'], 409);
            }
            throw $exception;
        }

        return $this->jsonResponse($this->formatCompany($row), 201);
    }

    public function suspendCompany(string $id): Response
    {
        return $this->setStatus($id, 'suspended');
    }

    public function activateCompany(string $id): Response
    {
        return $this->setStatus($id, 'active');
    }

    /**
     * Crypto-shredding — zeroes the company's wrapped DEK and records when, so its
     * connection secrets become permanently undecryptable (PRD §12, §7.7). Idempotent:
     * destroying an already-destroyed (or never-created) DEK is a no-op 200, not an
     * error - the end state the caller wants (no usable DEK) is already true either way.
     */
    public function destroyCompanyDek(string $id): Response
    {
        if (($error = $this->requirePlatformAdmin()) !== null) {
            return $error;
        }
        if (!$this->isUuid($id) || $this->findCompany($id) === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        $this->runInTransactionAsCompany(
            $id,
            static fn ($db) => $db->execute(
                "UPDATE runtime.company_keys SET wrapped_dek = ''::bytea, destroyed_at = now()
                 WHERE company_id = :company_id AND destroyed_at IS NULL",
                ['company_id' => $id]
            )
        );

        return $this->jsonResponse(['destroyed' => true]);
    }

    public function listCompanyMembers(string $id): Response
    {
        if (($error = $this->requirePlatformAdmin()) !== null) {
            return $error;
        }
        if (!$this->isUuid($id) || $this->findCompany($id) === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        $rows = $this->runInTransactionAsCompany(
            $id,
            static fn ($db) => $db->fetchAll(
                'SELECT u.id, u.email, u.is_platform_admin, cm.role
                 FROM console.company_members cm
                 JOIN console.users u ON u.id = cm.user_id
                 ORDER BY u.email',
                Enum::FETCH_ASSOC
            )
        );

        return $this->jsonResponse(array_map(
            static fn (array $row): array => [
                'id' => $row['id'],
                'email' => $row['email'],
                'is_platform_admin' => (bool) $row['is_platform_admin'],
                'role' => $row['role'],
            ],
            $rows
        ));
    }

    private function setStatus(string $id, string $status): Response
    {
        if (($error = $this->requirePlatformAdmin()) !== null) {
            return $error;
        }
        if (!$this->isUuid($id)) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        $db = $this->getDI()->getShared('db');
        $row = $db->fetchOne(
            'UPDATE console.companies SET status = :status, updated_at = now() WHERE id = :id
             RETURNING id, code, name, external_ref, status, monthly_budget_usd, created_at, updated_at',
            Enum::FETCH_ASSOC,
            ['status' => $status, 'id' => $id]
        );

        if (empty($row)) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        return $this->jsonResponse($this->formatCompany($row));
    }

    /**
     * @return array<string, mixed>|null
     */
    private function findCompany(string $id): ?array
    {
        $db = $this->getDI()->getShared('db');
        $row = $db->fetchOne(
            'SELECT id FROM console.companies WHERE id = :id',
            Enum::FETCH_ASSOC,
            ['id' => $id]
        );

        return empty($row) ? null : $row;
    }

    private function isUniqueViolation(Throwable $exception): bool
    {
        return $exception instanceof \PDOException && ($exception->errorInfo[0] ?? null) === '23505';
    }

    /**
     * @param array<string, mixed> $row
     * @return array<string, mixed>
     */
    private function formatCompany(array $row): array
    {
        return [
            'id' => $row['id'],
            'code' => $row['code'],
            'name' => $row['name'],
            'external_ref' => $row['external_ref'],
            'status' => $row['status'],
            'monthly_budget_usd' => $row['monthly_budget_usd'] !== null ? (float) $row['monthly_budget_usd'] : null,
            'created_at' => $row['created_at'],
            'updated_at' => $row['updated_at'],
        ];
    }
}
