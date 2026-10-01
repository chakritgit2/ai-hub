<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Db\Enum;
use Phalcon\Http\Response;

/**
 * /settings/* — per-company members list and egress allowlist (PRD §7.4, §8.1
 * `egress_allowlist` table). The allowlist itself is only CRUD here — enforcement (ai-
 * runtime's `SafeHttpClient` checking it before outbound calls) is separate, unbuilt work.
 */
class SettingsController extends ControllerBase
{
    public function listCompanyUsers(): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $rows = $this->runInCompanyTransaction(
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

    public function listEgressAllowlist(): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $rows = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchAll(
                'SELECT id, company_id, host_pattern, port, allow_private_ip, created_at, updated_at
                 FROM console.egress_allowlist ORDER BY created_at DESC',
                Enum::FETCH_ASSOC
            )
        );

        return $this->jsonResponse(array_map([$this, 'formatEntry'], $rows));
    }

    public function createEgressAllowlistEntry(): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireWriteRole()) !== null) {
            return $error;
        }

        $body = $this->request->getJsonRawBody(true) ?? [];
        $hostPattern = $body['host_pattern'] ?? null;
        if (!is_string($hostPattern) || trim($hostPattern) === '') {
            return $this->jsonResponse(['error' => 'host_pattern_required'], 400);
        }
        $port = null;
        if (array_key_exists('port', $body) && $body['port'] !== null) {
            if (!is_int($body['port']) || $body['port'] < 1 || $body['port'] > 65535) {
                return $this->jsonResponse(['error' => 'invalid_port'], 400);
            }
            $port = $body['port'];
        }
        $allowPrivateIp = false;
        if (array_key_exists('allow_private_ip', $body)) {
            if (!is_bool($body['allow_private_ip'])) {
                return $this->jsonResponse(['error' => 'invalid_allow_private_ip'], 400);
            }
            $allowPrivateIp = $body['allow_private_ip'];
        }

        $row = $this->runInCompanyTransaction(
            fn ($db) => $db->fetchOne(
                'INSERT INTO console.egress_allowlist (company_id, host_pattern, port, allow_private_ip)
                 VALUES (:company_id, :host_pattern, :port, :allow_private_ip)
                 RETURNING id, company_id, host_pattern, port, allow_private_ip, created_at, updated_at',
                Enum::FETCH_ASSOC,
                [
                    'company_id' => $this->getCompanyId(),
                    'host_pattern' => $hostPattern,
                    'port' => $port,
                    // PDO casts a bound PHP `false` to '' (empty string), which Postgres
                    // rejects for a boolean column - bind the literal strings instead
                    // (see DeploymentsController::boolParam's note on the same gotcha).
                    'allow_private_ip' => $allowPrivateIp ? 'true' : 'false',
                ]
            )
        );

        return $this->jsonResponse($this->formatEntry($row), 201);
    }

    public function deleteEgressAllowlistEntry(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireWriteRole()) !== null) {
            return $error;
        }
        if (!$this->isUuid($id)) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        $deleted = $this->runInCompanyTransaction(function ($db) use ($id) {
            $db->execute('DELETE FROM console.egress_allowlist WHERE id = :id', ['id' => $id]);

            return $db->affectedRows() > 0;
        });

        if (!$deleted) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        return $this->noContentResponse();
    }

    /**
     * @param array<string, mixed> $row
     * @return array<string, mixed>
     */
    private function formatEntry(array $row): array
    {
        return [
            'id' => $row['id'],
            'company_id' => $row['company_id'],
            'host_pattern' => $row['host_pattern'],
            'port' => $row['port'] !== null ? (int) $row['port'] : null,
            'allow_private_ip' => (bool) $row['allow_private_ip'],
            'created_at' => $row['created_at'],
            'updated_at' => $row['updated_at'],
        ];
    }
}
