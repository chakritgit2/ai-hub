<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Db\Enum;
use Phalcon\Http\Response;
use Throwable;

/**
 * /tenants — platform_admin only, no X-Company-Id (same shape as CompaniesController).
 * `console.tenants` has no RLS, same reasoning as `console.companies`/`console.users`
 * (PRD §7.3: company-scoping applies to rows *belonging to* a company, not to this table).
 * No update/rename endpoint, same deliberate minimal-surface choice as Companies -
 * rename via a plain SQL UPDATE if ever needed.
 */
class TenantsController extends ControllerBase
{
    public function listTenants(): Response
    {
        if (($error = $this->requirePlatformAdmin()) !== null) {
            return $error;
        }

        $db = $this->getDI()->getShared('db');
        $rows = $db->fetchAll(
            'SELECT id, code, name, status, created_at, updated_at FROM console.tenants ORDER BY created_at DESC',
            Enum::FETCH_ASSOC
        );

        return $this->jsonResponse(array_map([$this, 'formatTenant'], $rows));
    }

    public function createTenant(): Response
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

        $db = $this->getDI()->getShared('db');
        try {
            $row = $db->fetchOne(
                'INSERT INTO console.tenants (code, name)
                 VALUES (:code, :name)
                 RETURNING id, code, name, status, created_at, updated_at',
                Enum::FETCH_ASSOC,
                ['code' => $code, 'name' => $name]
            );
        } catch (Throwable $exception) {
            if ($this->isUniqueViolation($exception)) {
                return $this->jsonResponse(['error' => 'code_already_exists'], 409);
            }
            throw $exception;
        }

        return $this->jsonResponse($this->formatTenant($row), 201);
    }

    public function suspendTenant(string $id): Response
    {
        return $this->setStatus($id, 'suspended');
    }

    public function activateTenant(string $id): Response
    {
        return $this->setStatus($id, 'active');
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
            'UPDATE console.tenants SET status = :status, updated_at = now() WHERE id = :id
             RETURNING id, code, name, status, created_at, updated_at',
            Enum::FETCH_ASSOC,
            ['status' => $status, 'id' => $id]
        );

        if (empty($row)) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        return $this->jsonResponse($this->formatTenant($row));
    }

    private function isUniqueViolation(Throwable $exception): bool
    {
        return $exception instanceof \PDOException && ($exception->errorInfo[0] ?? null) === '23505';
    }

    /**
     * @param array<string, mixed> $row
     * @return array<string, mixed>
     */
    private function formatTenant(array $row): array
    {
        return [
            'id' => $row['id'],
            'code' => $row['code'],
            'name' => $row['name'],
            'status' => $row['status'],
            'created_at' => $row['created_at'],
            'updated_at' => $row['updated_at'],
        ];
    }
}
