<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Db\Enum;
use Phalcon\Http\Response;

/**
 * /api-keys — long-lived API keys used by external apps against the gateway
 * (PRD §6.3, §8.1 `api_keys` table; resolved via `console.resolve_api_key()` at the
 * gateway). Admin-only (PRD §9.3's role table scopes the whole resource to `admin`, not
 * `developer`) — these are the credentials external callers use, a step more sensitive
 * than agents/connections/deployments.
 *
 * The full key is generated here, hashed with SHA-256 and only the hash
 * (`key_hash`) is ever persisted; the plaintext is returned exactly once, on
 * createApiKey, and never stored or logged anywhere afterward.
 */
class ApiKeysController extends ControllerBase
{
    public function listApiKeys(): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireAdminRole()) !== null) {
            return $error;
        }

        $rows = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchAll(
                "SELECT k.id, k.company_id, k.name, k.prefix, k.allowed_ips, k.rate_limit_per_min,
                        k.daily_cost_limit_usd, k.last_used_at, k.created_by, k.revoked_at, k.created_at,
                        coalesce(array_agg(s.deployment_id) FILTER (WHERE s.deployment_id IS NOT NULL), '{}') AS deployment_ids
                 FROM console.api_keys k
                 LEFT JOIN console.api_key_scopes s ON s.api_key_id = k.id
                 GROUP BY k.id
                 ORDER BY k.created_at DESC",
                Enum::FETCH_ASSOC
            )
        );

        return $this->jsonResponse(array_map(
            fn (array $row) => [...$this->formatApiKey($row), 'deployment_ids' => $this->parsePgUuidArray($row['deployment_ids'])],
            $rows
        ));
    }

    public function createApiKey(): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireAdminRole()) !== null) {
            return $error;
        }

        $body = $this->request->getJsonRawBody(true) ?? [];
        $name = $body['name'] ?? null;
        if (!is_string($name) || trim($name) === '') {
            return $this->jsonResponse(['error' => 'name_required'], 400);
        }
        $allowedIps = [];
        if (array_key_exists('allowed_ips', $body)) {
            if (!is_array($body['allowed_ips']) || !$this->isStringList($body['allowed_ips'])) {
                return $this->jsonResponse(['error' => 'invalid_allowed_ips'], 400);
            }
            $allowedIps = $body['allowed_ips'];
        }
        $rateLimitPerMin = null;
        if (array_key_exists('rate_limit_per_min', $body) && $body['rate_limit_per_min'] !== null) {
            if (!is_int($body['rate_limit_per_min']) || $body['rate_limit_per_min'] < 1) {
                return $this->jsonResponse(['error' => 'invalid_rate_limit_per_min'], 400);
            }
            $rateLimitPerMin = $body['rate_limit_per_min'];
        }
        $dailyCostLimitUsd = null;
        if (array_key_exists('daily_cost_limit_usd', $body) && $body['daily_cost_limit_usd'] !== null) {
            if (!is_numeric($body['daily_cost_limit_usd'])) {
                return $this->jsonResponse(['error' => 'invalid_daily_cost_limit_usd'], 400);
            }
            $dailyCostLimitUsd = $body['daily_cost_limit_usd'];
        }
        // A key with no deployment_ids is a valid-but-useless state (console.resolve_api_key
        // never matches it anywhere, PRD §7.7) rather than a validation error - the admin
        // may be staging a key before any deployment exists yet.
        $deploymentIds = [];
        if (array_key_exists('deployment_ids', $body)) {
            if (!is_array($body['deployment_ids']) || !$this->isStringList($body['deployment_ids'])) {
                return $this->jsonResponse(['error' => 'invalid_deployment_ids'], 400);
            }
            foreach ($body['deployment_ids'] as $deploymentId) {
                if (!$this->isUuid($deploymentId)) {
                    return $this->jsonResponse(['error' => 'invalid_deployment_ids'], 400);
                }
            }
            $deploymentIds = array_values(array_unique($body['deployment_ids']));
        }

        $userId = $this->getAuthUser()['id'];

        try {
            $result = $this->runInCompanyTransaction(function ($db) use (
                $name, $allowedIps, $rateLimitPerMin, $dailyCostLimitUsd, $deploymentIds, $userId
            ) {
                $company = $db->fetchOne(
                    'SELECT code FROM console.companies WHERE id = :id',
                    Enum::FETCH_ASSOC,
                    ['id' => $this->getCompanyId()]
                );

                // bin2hex(random_bytes(24)) — 192 bits of entropy, more than enough that a
                // plain SHA-256 hash (no salt/pepper) of the full key is safe to store: unlike
                // a user password, this secret is never guessable or reused, so brute-forcing
                // the hash is infeasible regardless of salting.
                $secret = bin2hex(random_bytes(24));
                $prefix = 'ak_' . $company['code'] . '_';
                $fullKey = $prefix . $secret;
                $keyHash = hash('sha256', $fullKey);

                $row = $db->fetchOne(
                    'INSERT INTO console.api_keys
                        (company_id, name, key_hash, prefix, allowed_ips, rate_limit_per_min, daily_cost_limit_usd, created_by)
                     VALUES
                        (:company_id, :name, :key_hash, :prefix, :allowed_ips::jsonb, :rate_limit_per_min, :daily_cost_limit_usd, :created_by)
                     RETURNING id, company_id, name, prefix, allowed_ips, rate_limit_per_min, daily_cost_limit_usd,
                               last_used_at, created_by, revoked_at, created_at',
                    Enum::FETCH_ASSOC,
                    [
                        'company_id' => $this->getCompanyId(),
                        'name' => $name,
                        'key_hash' => $keyHash,
                        'prefix' => $prefix,
                        'allowed_ips' => json_encode($allowedIps),
                        'rate_limit_per_min' => $rateLimitPerMin,
                        'daily_cost_limit_usd' => $dailyCostLimitUsd,
                        'created_by' => $userId,
                    ]
                );

                foreach ($deploymentIds as $deploymentId) {
                    // RLS on console.deployments (scoped to the same app.company_id this
                    // transaction already set) makes a deployment belonging to another
                    // company invisible here - the INSERT's FK would fail loudly instead
                    // of silently scoping a key to someone else's deployment.
                    $deployment = $db->fetchOne(
                        'SELECT id FROM console.deployments WHERE id = :id',
                        Enum::FETCH_ASSOC,
                        ['id' => $deploymentId]
                    );
                    if (empty($deployment)) {
                        throw new \RuntimeException("deployment_not_found:{$deploymentId}");
                    }
                    try {
                        $db->execute(
                            'INSERT INTO console.api_key_scopes (company_id, api_key_id, deployment_id)
                             VALUES (:company_id, :api_key_id, :deployment_id)',
                            [
                                'company_id' => $this->getCompanyId(),
                                'api_key_id' => $row['id'],
                                'deployment_id' => $deploymentId,
                            ]
                        );
                    } catch (\Throwable $exception) {
                        if ($this->isUniqueViolation($exception)) {
                            // deployment_ids is deduped with array_unique() above, which is
                            // case-sensitive - two UUIDs differing only by letter case pass
                            // that dedup as "different" but collide on the same row here.
                            throw new \RuntimeException("duplicate_deployment_id:{$deploymentId}");
                        }
                        throw $exception;
                    }
                }

                return ['row' => $row, 'full_key' => $fullKey, 'deployment_ids' => $deploymentIds];
            });
        } catch (\RuntimeException $exception) {
            if (str_starts_with($exception->getMessage(), 'deployment_not_found:')) {
                return $this->jsonResponse(['error' => 'deployment_not_found'], 400);
            }
            if (str_starts_with($exception->getMessage(), 'duplicate_deployment_id:')) {
                return $this->jsonResponse(['error' => 'duplicate_deployment_id'], 400);
            }
            throw $exception;
        }

        return $this->jsonResponse(
            [...$this->formatApiKey($result['row']), 'deployment_ids' => $result['deployment_ids'], 'key' => $result['full_key']],
            201
        );
    }

    public function revokeApiKey(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireAdminRole()) !== null) {
            return $error;
        }
        if ($this->findApiKey($id) === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        $row = $this->runInCompanyTransaction(function ($db) use ($id) {
            // Idempotent: revoking an already-revoked key is a no-op, not an error — the
            // caller's intent (this key must not work) is already satisfied either way.
            $db->execute(
                'UPDATE console.api_keys SET revoked_at = now() WHERE id = :id AND revoked_at IS NULL',
                ['id' => $id]
            );

            return $db->fetchOne(
                'SELECT id, company_id, name, prefix, allowed_ips, rate_limit_per_min, daily_cost_limit_usd,
                        last_used_at, created_by, revoked_at, created_at
                 FROM console.api_keys WHERE id = :id',
                Enum::FETCH_ASSOC,
                ['id' => $id]
            );
        });

        return $this->jsonResponse($this->formatApiKey($row));
    }

    private function isStringList(array $value): bool
    {
        foreach ($value as $item) {
            if (!is_string($item)) {
                return false;
            }
        }

        return true;
    }

    private function isUniqueViolation(\Throwable $exception): bool
    {
        return $exception instanceof \PDOException && ($exception->errorInfo[0] ?? null) === '23505';
    }

    /**
     * Postgres's `array_agg(uuid)` comes back over PDO as a literal `{uuid1,uuid2}`
     * string, not a PHP array the way a jsonb column would - '{}' (no scopes) parses to [].
     *
     * @return list<string>
     */
    private function parsePgUuidArray(string $pgArrayLiteral): array
    {
        $trimmed = trim($pgArrayLiteral, '{}');

        return $trimmed === '' ? [] : explode(',', $trimmed);
    }

    /**
     * @return array<string, mixed>|null
     */
    private function findApiKey(string $id): ?array
    {
        if (!$this->isUuid($id)) {
            return null;
        }

        $row = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchOne(
                'SELECT id, company_id, name, prefix, allowed_ips, rate_limit_per_min, daily_cost_limit_usd,
                        last_used_at, created_by, revoked_at, created_at
                 FROM console.api_keys WHERE id = :id',
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
    private function formatApiKey(array $row): array
    {
        return [
            'id' => $row['id'],
            'company_id' => $row['company_id'],
            'name' => $row['name'],
            'prefix' => $row['prefix'],
            'allowed_ips' => json_decode((string) $row['allowed_ips'], true),
            'rate_limit_per_min' => $row['rate_limit_per_min'] !== null ? (int) $row['rate_limit_per_min'] : null,
            'daily_cost_limit_usd' => $row['daily_cost_limit_usd'] !== null ? (float) $row['daily_cost_limit_usd'] : null,
            'last_used_at' => $row['last_used_at'],
            'created_by' => $row['created_by'],
            'revoked_at' => $row['revoked_at'],
            'created_at' => $row['created_at'],
        ];
    }
}
