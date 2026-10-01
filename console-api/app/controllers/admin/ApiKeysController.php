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
                'SELECT id, company_id, name, prefix, allowed_ips, rate_limit_per_min, daily_cost_limit_usd,
                        last_used_at, created_by, revoked_at, created_at
                 FROM console.api_keys ORDER BY created_at DESC',
                Enum::FETCH_ASSOC
            )
        );

        return $this->jsonResponse(array_map([$this, 'formatApiKey'], $rows));
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

        $userId = $this->getAuthUser()['id'];

        $result = $this->runInCompanyTransaction(function ($db) use ($name, $allowedIps, $rateLimitPerMin, $dailyCostLimitUsd, $userId) {
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

            return ['row' => $row, 'full_key' => $fullKey];
        });

        return $this->jsonResponse(
            [...$this->formatApiKey($result['row']), 'key' => $result['full_key']],
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
