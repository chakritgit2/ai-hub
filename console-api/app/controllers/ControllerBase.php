<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers;

use ConsoleApi\Services\CompanyContext;
use Phalcon\Db\Adapter\Pdo\AbstractPdo;
use Phalcon\Http\Response;
use Phalcon\Mvc\Controller;
use RuntimeException;
use Throwable;

abstract class ControllerBase extends Controller
{
    protected function jsonResponse(mixed $data, int $statusCode = 200): Response
    {
        $response = new Response();
        $response->setStatusCode($statusCode);
        $response->setContentType('application/json');
        $response->setJsonContent($data);

        return $response;
    }

    /**
     * 204 No Content — unlike jsonResponse(null, 204), sends no body at all; a 204
     * response must not have one per HTTP semantics.
     */
    protected function noContentResponse(): Response
    {
        $response = new Response();
        $response->setStatusCode(204);

        return $response;
    }

    /**
     * Standard body for every x-phase: 2 / x-phase: 3 (and otherwise unbuilt) operation:
     * HTTP 501 with {"error": "not_implemented"}.
     */
    protected function notImplemented(): Response
    {
        return $this->jsonResponse(['error' => 'not_implemented'], 501);
    }

    /**
     * The company_id bound from the X-Company-Id header by CompanyContextMiddleware
     * (PRD §7.2). Null on routes that don't require it (/me, /companies).
     */
    protected function getCompanyId(): ?string
    {
        /** @var CompanyContext $context */
        $context = $this->getDI()->getShared('companyContext');

        return $context->getCompanyId();
    }

    /**
     * The authenticated user's synced record, attached by AuthMiddleware after verifying
     * the SSO JWT (PRD §7.1). Null only on the exempt /healthz route.
     *
     * @return array{
     *     id: string,
     *     email: string,
     *     is_platform_admin: bool,
     *     companies: array<int, array{company_id: string, role: string}>,
     *     skipped_company_refs: array<int, string>
     * }|null
     */
    protected function getAuthUser(): ?array
    {
        return $this->dispatcher->getParam('authUser');
    }

    /**
     * The authenticated user's role within the current X-Company-Id (PRD §7.2) — null if
     * either is missing, or if they aren't actually a member of this company (callers
     * should treat that as "not found", same as RuntimeTokenController does, rather than
     * leaking that the company exists).
     */
    protected function getRoleForCurrentCompany(): ?string
    {
        $companyId = $this->getCompanyId();
        $authUser = $this->getAuthUser();
        if ($companyId === null || $authUser === null) {
            return null;
        }

        foreach ($authUser['companies'] as $membership) {
            if ($membership['company_id'] === $companyId) {
                return $membership['role'];
            }
        }

        return null;
    }

    protected function requireCompanyId(): ?Response
    {
        if ($this->getCompanyId() === null) {
            return $this->jsonResponse(['error' => 'company_id_required'], 400);
        }

        return null;
    }

    /**
     * 404s unless the authenticated user is actually a member of the current
     * X-Company-Id — every company-scoped endpoint (read or write) must call this (or
     * requireWriteRole()), not just requireCompanyId(). RLS scopes a query by whatever
     * app.company_id happens to be set to, not by the caller's real membership, so
     * without this check a user could read another company's data just by sending that
     * company's id in the header (PRD §7.3's application-layer check, on top of RLS).
     */
    protected function requireMembership(): ?Response
    {
        if ($this->getRoleForCurrentCompany() === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        return null;
    }

    /**
     * admin/developer can write; viewer is read-only everywhere (PRD §7.2). Not a member
     * of the current company at all reads the same as "not found" (PRD §12).
     */
    protected function requireWriteRole(): ?Response
    {
        $role = $this->getRoleForCurrentCompany();
        if ($role === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }
        if ($role === 'viewer') {
            return $this->jsonResponse(['error' => 'forbidden'], 403);
        }

        return null;
    }

    /**
     * admin-only — e.g. /api-keys, which PRD §9.3's role table scopes to admin alone
     * (not developer). Not a member of the current company at all reads the same as
     * "not found" (PRD §12).
     */
    protected function requireAdminRole(): ?Response
    {
        $role = $this->getRoleForCurrentCompany();
        if ($role === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }
        if ($role !== 'admin') {
            return $this->jsonResponse(['error' => 'forbidden'], 403);
        }

        return null;
    }

    /**
     * platform_admin-only — /companies/* (PRD §7.2/§9.3: "platform_admin only; no
     * X-Company-Id"). Unlike requireMembership()/requireWriteRole()/requireAdminRole(),
     * this has no company to pretend doesn't exist for a 404 — it's a flat authorization
     * boundary, so a non-platform-admin gets 403, not 404.
     */
    protected function requirePlatformAdmin(): ?Response
    {
        $authUser = $this->getAuthUser();
        if ($authUser === null || !($authUser['is_platform_admin'] ?? false)) {
            return $this->jsonResponse(['error' => 'forbidden'], 403);
        }

        return null;
    }

    /**
     * Strict UUID format (8-4-4-4-12 hex groups) — not just "36 characters of hex digits
     * and dashes in any position", which Postgres's `uuid` column type itself rejects
     * with an uncaught SQLSTATE 22P02 (never caught by isUniqueViolation()'s 23505 check),
     * turning what should be a 404 into a 500.
     */
    protected function isUuid(string $value): bool
    {
        return (bool) preg_match('/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i', $value);
    }

    /**
     * Runs $fn($db) inside a transaction with CompanyContext applied (PRD §7.3) — the
     * pattern every company-scoped query in this codebase needs, since
     * console.*'s FORCE ROW LEVEL SECURITY only sees rows matching `app.company_id`.
     * Commits on success, rolls back and rethrows on any exception.
     *
     * @template T
     * @param callable(AbstractPdo): T $fn
     * @return T
     */
    protected function runInCompanyTransaction(callable $fn): mixed
    {
        $companyId = $this->getCompanyId();
        if ($companyId === null) {
            throw new RuntimeException('runInCompanyTransaction: no X-Company-Id bound for this request.');
        }

        return $this->runInTransactionAsCompany($companyId, $fn);
    }

    /**
     * Same as runInCompanyTransaction(), but for platform_admin operations that act on an
     * explicit company id (e.g. a `/companies/{id}/...` path param) rather than the
     * current request's X-Company-Id header — there is none on platform-only routes
     * (requirePlatformAdmin()), so this is how CompaniesController reaches into one
     * specific company's RLS-protected rows (console.company_members, runtime.company_keys)
     * without needing the unwired console_platform DB connection.
     *
     * @template T
     * @param callable(AbstractPdo): T $fn
     * @return T
     */
    protected function runInTransactionAsCompany(string $companyId, callable $fn): mixed
    {
        $db = $this->getDI()->getShared('db');
        $db->begin();

        try {
            $context = new CompanyContext();
            $context->setCompanyId($companyId);
            $context->applyToConnection($db);

            $result = $fn($db);

            $db->commit();

            return $result;
        } catch (Throwable $exception) {
            $db->rollback();

            throw $exception;
        }
    }
}
