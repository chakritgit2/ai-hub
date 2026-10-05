<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use ConsoleApi\Services\EgressAllowlistMatcher;
use ConsoleApi\Services\RuntimeClient;
use Phalcon\Db\Enum;
use Phalcon\Http\Response;
use RuntimeException;
use Throwable;

/**
 * /connections — company-scoped LLM/tool connections (PRD §7.4, §8.1 `connections` table).
 * Secrets are never persisted by console-api; putConnectionSecret forwards internally to
 * ai-runtime's `PUT /internal/v1/connections/{id}/secret`, which envelope-encrypts them
 * with the company's own DEK (PRD §7.7). createConnection/updateConnection only ever
 * touch metadata — a `secret` field in the request body is ignored.
 */
class ConnectionsController extends ControllerBase
{
    public function listConnections(): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $rows = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchAll(
                'SELECT id, company_id, name, type, api_base, masked_hint, max_concurrency, meta,
                        created_at, updated_at
                 FROM console.connections ORDER BY created_at DESC',
                Enum::FETCH_ASSOC
            )
        );

        return $this->jsonResponse(array_map([$this, 'formatConnection'], $rows));
    }

    public function createConnection(): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireWriteRole()) !== null) {
            return $error;
        }

        $body = $this->request->getJsonRawBody(true) ?? [];
        $name = $body['name'] ?? null;
        if (!is_string($name) || trim($name) === '') {
            return $this->jsonResponse(['error' => 'name_required'], 400);
        }
        $type = $body['type'] ?? null;
        if (!is_string($type) || trim($type) === '') {
            return $this->jsonResponse(['error' => 'type_required'], 400);
        }
        $apiBase = is_string($body['api_base'] ?? null) ? $body['api_base'] : null;
        $maxConcurrency = is_int($body['max_concurrency'] ?? null) ? $body['max_concurrency'] : 4;
        $meta = is_array($body['meta'] ?? null) ? $body['meta'] : [];

        if (($error = $this->assertApiBaseAllowed($apiBase)) !== null) {
            return $error;
        }

        try {
            $row = $this->runInCompanyTransaction(
                fn ($db) => $db->fetchOne(
                    'INSERT INTO console.connections (company_id, name, type, api_base, max_concurrency, meta)
                     VALUES (:company_id, :name, :type, :api_base, :max_concurrency, :meta::jsonb)
                     RETURNING id, company_id, name, type, api_base, masked_hint, max_concurrency, meta,
                               created_at, updated_at',
                    Enum::FETCH_ASSOC,
                    [
                        'company_id' => $this->getCompanyId(),
                        'name' => $name,
                        'type' => $type,
                        'api_base' => $apiBase,
                        'max_concurrency' => $maxConcurrency,
                        'meta' => json_encode($meta),
                    ]
                )
            );
        } catch (Throwable $exception) {
            if ($this->isUniqueViolation($exception)) {
                return $this->jsonResponse(['error' => 'name_already_exists'], 409);
            }
            throw $exception;
        }

        return $this->jsonResponse($this->formatConnection($row), 201);
    }

    public function getConnection(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $row = $this->findConnection($id);
        if ($row === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        return $this->jsonResponse($this->formatConnection($row));
    }

    public function updateConnection(string $id): Response
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

        $body = $this->request->getJsonRawBody(true) ?? [];
        $fields = [];
        if (array_key_exists('name', $body)) {
            if (!is_string($body['name']) || trim($body['name']) === '') {
                return $this->jsonResponse(['error' => 'name_required'], 400);
            }
            $fields['name'] = $body['name'];
        }
        if (array_key_exists('type', $body)) {
            if (!is_string($body['type']) || trim($body['type']) === '') {
                return $this->jsonResponse(['error' => 'type_required'], 400);
            }
            $fields['type'] = $body['type'];
        }
        if (array_key_exists('api_base', $body)) {
            $apiBase = is_string($body['api_base']) ? $body['api_base'] : null;
            if (($error = $this->assertApiBaseAllowed($apiBase)) !== null) {
                return $error;
            }
            $fields['api_base'] = $apiBase;
        }
        if (array_key_exists('max_concurrency', $body)) {
            if (!is_int($body['max_concurrency'])) {
                return $this->jsonResponse(['error' => 'max_concurrency_must_be_integer'], 400);
            }
            $fields['max_concurrency'] = $body['max_concurrency'];
        }
        if (array_key_exists('meta', $body)) {
            if (!is_array($body['meta'])) {
                return $this->jsonResponse(['error' => 'meta_must_be_object'], 400);
            }
            $fields['meta'] = json_encode($body['meta']);
        }

        try {
            $row = $this->runInCompanyTransaction(function ($db) use ($id, $fields) {
                if ($fields !== []) {
                    $params = ['id' => $id];
                    $setClauses = [];
                    foreach ($fields as $column => $value) {
                        $placeholder = $column === 'meta' ? ":{$column}::jsonb" : ":{$column}";
                        $setClauses[] = "{$column} = {$placeholder}";
                        $params[$column] = $value;
                    }
                    $setClauses[] = 'updated_at = now()';
                    $db->execute(
                        'UPDATE console.connections SET ' . implode(', ', $setClauses) . ' WHERE id = :id',
                        $params
                    );
                }

                return $db->fetchOne(
                    'SELECT id, company_id, name, type, api_base, masked_hint, max_concurrency, meta,
                            created_at, updated_at
                     FROM console.connections WHERE id = :id',
                    Enum::FETCH_ASSOC,
                    ['id' => $id]
                );
            });
        } catch (Throwable $exception) {
            if ($this->isUniqueViolation($exception)) {
                return $this->jsonResponse(['error' => 'name_already_exists'], 409);
            }
            throw $exception;
        }

        if (empty($row)) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        return $this->jsonResponse($this->formatConnection($row));
    }

    /**
     * 409s with the dependent agent names if any agent version (published or not)
     * still references this connection (PRD §12) — deleting out from under a compiled
     * agent would leave it unable to run.
     */
    public function deleteConnection(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireWriteRole()) !== null) {
            return $error;
        }
        if ($this->findConnection($id) === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        $result = $this->runInCompanyTransaction(function ($db) use ($id) {
            $dependents = $db->fetchAll(
                "SELECT DISTINCT a.name
                 FROM console.agent_versions av
                 JOIN console.agents a ON a.id = av.agent_id
                 WHERE av.spec -> 'model' ->> 'connection_id' = :connection_id",
                Enum::FETCH_ASSOC,
                ['connection_id' => $id]
            );
            if ($dependents !== []) {
                return ['deleted' => false, 'dependents' => array_column($dependents, 'name')];
            }

            $db->execute('DELETE FROM console.connections WHERE id = :id', ['id' => $id]);

            return ['deleted' => true, 'dependents' => []];
        });

        if (!$result['deleted']) {
            return $this->jsonResponse(
                ['error' => 'connection_in_use', 'agents' => $result['dependents']],
                409
            );
        }

        return $this->noContentResponse();
    }

    /**
     * Validates length, forwards to ai-runtime (outside any DB transaction — never hold
     * one open across an HTTP call), then records only a masked hint on success
     * (PRD §7.4). The plaintext secret never touches console-api's own storage.
     */
    public function putConnectionSecret(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireWriteRole()) !== null) {
            return $error;
        }
        if ($this->findConnection($id) === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        $body = $this->request->getJsonRawBody(true) ?? [];
        $secret = $body['secret'] ?? null;
        if (!is_string($secret) || strlen($secret) < 8) {
            return $this->jsonResponse(['error' => 'secret_too_short'], 400);
        }

        /** @var RuntimeClient $runtimeClient */
        $runtimeClient = $this->getDI()->getShared('runtimeClient');

        try {
            $runtimeClient->putConnectionSecret($id, $secret);
        } catch (RuntimeException $exception) {
            return $this->jsonResponse(
                ['error' => 'secret_store_failed', 'message' => $exception->getMessage()],
                502
            );
        }

        $maskedHint = '••••' . substr($secret, -4);
        $this->runInCompanyTransaction(
            static fn ($db) => $db->execute(
                'UPDATE console.connections SET masked_hint = :masked_hint, updated_at = now() WHERE id = :id',
                ['masked_hint' => $maskedHint, 'id' => $id]
            )
        );

        return $this->noContentResponse();
    }

    public function testConnection(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireWriteRole()) !== null) {
            return $error;
        }
        if ($this->findConnection($id) === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        /** @var RuntimeClient $runtimeClient */
        $runtimeClient = $this->getDI()->getShared('runtimeClient');

        try {
            $result = $runtimeClient->testConnection($id);
        } catch (RuntimeException $exception) {
            return $this->jsonResponse(
                ['error' => 'test_request_failed', 'message' => $exception->getMessage()],
                502
            );
        }

        return $this->jsonResponse($result);
    }

    private function isUniqueViolation(Throwable $exception): bool
    {
        return $exception instanceof \PDOException && ($exception->errorInfo[0] ?? null) === '23505';
    }

    /**
     * Save-time half of PRD §7.4's "every connection's api_base must pass the egress
     * allowlist on save and at run time" — host-pattern match only (see
     * EgressAllowlistMatcher); ai-runtime's SafeHttpClient re-checks the resolved IP at
     * request time. `null` (no api_base given) is always allowed — nothing to validate.
     */
    private function assertApiBaseAllowed(?string $apiBase): ?Response
    {
        if ($apiBase === null) {
            return null;
        }

        $host = parse_url($apiBase, PHP_URL_HOST);
        if (!is_string($host) || $host === '') {
            return $this->jsonResponse(['error' => 'api_base_invalid'], 400);
        }

        $rows = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchAll(
                'SELECT host_pattern, port, allow_private_ip FROM console.egress_allowlist',
                Enum::FETCH_ASSOC
            )
        );

        if (!EgressAllowlistMatcher::hostAllowed($rows, $host)) {
            return $this->jsonResponse(['error' => 'egress_host_not_allowed', 'host' => $host], 422);
        }

        return null;
    }

    /**
     * @return array<string, mixed>|null
     */
    private function findConnection(string $id): ?array
    {
        if (!$this->isUuid($id)) {
            return null;
        }

        $row = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchOne(
                'SELECT id, company_id, name, type, api_base, masked_hint, max_concurrency, meta,
                        created_at, updated_at
                 FROM console.connections WHERE id = :id',
                Enum::FETCH_ASSOC,
                ['id' => $id]
            )
        );

        return empty($row) ? null : $row;
    }

    /**
     * @param array<string, mixed> $row
     * @return array<string, mixed>
     */
    private function formatConnection(array $row): array
    {
        return [
            'id' => $row['id'],
            'company_id' => $row['company_id'],
            'name' => $row['name'],
            'type' => $row['type'],
            'api_base' => $row['api_base'],
            'masked_hint' => $row['masked_hint'],
            'max_concurrency' => (int) $row['max_concurrency'],
            'meta' => json_decode((string) $row['meta'], true),
            'created_at' => $row['created_at'],
            'updated_at' => $row['updated_at'],
        ];
    }
}
