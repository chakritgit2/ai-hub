<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use ConsoleApi\Services\AlertThresholds;
use Phalcon\Db\Enum;
use Phalcon\Http\Response;

/**
 * /settings/* — per-company members list and egress allowlist (PRD §7.4, §8.1
 * `egress_allowlist` table). The allowlist itself is only CRUD here — enforcement (ai-
 * runtime's `SafeHttpClient` checking it before outbound calls) is separate, unbuilt work.
 *
 * The allowlist's mutators are admin-only (PRD §9.1's role table scopes `/settings` to
 * `admin`, not `developer`) — same reasoning as `/api-keys`: a wider allowlist entry
 * (especially `allow_private_ip`) widens every tool/connection's egress surface for the
 * whole company, not just the thing the caller is working on.
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

    /**
     * `console.users.is_platform_admin` is a flat, global flag - not company-scoped and
     * never touched by `AuthService::syncUser()`'s per-request SSO upsert (unlike
     * `company_members.role`, which that upsert overwrites from the JWT's `companies`
     * claim on every request - see that method's own `ON CONFLICT ... DO UPDATE SET
     * role`). That's why this is the one piece of "who can do what" this console can
     * manage directly today without it being silently reverted, and why it's
     * platform_admin-gated like Tenants/Companies rather than company-admin-gated like
     * the rest of this controller.
     */
    public function grantPlatformAdmin(string $id): Response
    {
        return $this->setPlatformAdmin($id, true);
    }

    public function revokePlatformAdmin(string $id): Response
    {
        return $this->setPlatformAdmin($id, false);
    }

    private function setPlatformAdmin(string $id, bool $isPlatformAdmin): Response
    {
        if (($error = $this->requirePlatformAdmin()) !== null) {
            return $error;
        }
        if (!$this->isUuid($id)) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }
        // A platform_admin revoking their OWN flag is the one click that can leave the
        // product with zero platform_admins and no one left who can grant it back (no
        // recovery path short of a direct DB write) - revoking someone else's is always
        // safe, since the caller themselves remains a platform_admin to fix it afterwards.
        // Case-insensitive: isUuid() accepts either case and Postgres's uuid type compares
        // case-insensitively too, so a same-UUID-different-case `$id` would otherwise slip
        // past a plain `===` and defeat this guard entirely.
        if (!$isPlatformAdmin && strcasecmp($id, $this->getAuthUser()['id']) === 0) {
            return $this->jsonResponse(['error' => 'cannot_revoke_self'], 400);
        }

        $db = $this->getDI()->getShared('db');
        $row = $db->fetchOne(
            'UPDATE console.users SET is_platform_admin = :is_platform_admin, updated_at = now()
             WHERE id = :id
             RETURNING id, email, is_platform_admin',
            Enum::FETCH_ASSOC,
            [
                // PDO casts a bound PHP `false` to '' (empty string), which Postgres
                // rejects for a boolean column - bind the literal strings instead (see
                // createEgressAllowlistEntry's allow_private_ip for the same gotcha).
                'is_platform_admin' => $isPlatformAdmin ? 'true' : 'false',
                'id' => $id,
            ]
        );

        if (empty($row)) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        return $this->jsonResponse([
            'id' => $row['id'],
            'email' => $row['email'],
            'is_platform_admin' => (bool) $row['is_platform_admin'],
        ]);
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
        if (($error = $this->requireAdminRole()) !== null) {
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
        if (($error = $this->requireAdminRole()) !== null) {
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
     * Any member can read the thresholds (same visibility as the Dashboard's other
     * figures these drive) - AlertThresholds::resolve() returns the built-in defaults
     * when this company has never customized them, so the response shape never has
     * missing keys for the frontend to special-case.
     */
    public function getAlertThresholds(): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        return $this->jsonResponse(
            $this->runInCompanyTransaction(static fn ($db) => AlertThresholds::resolve($db))
        );
    }

    /**
     * Admin-only (PRD §9.1-style scoping, same as every other mutator in this
     * controller) - a wider/looser threshold here silences a real signal for the whole
     * company, not just the caller's own view of it.
     *
     * Every field is optional in the request body (a partial update keeps this
     * company's existing values for anything omitted, upsert-merged via the INSERT ...
     * ON CONFLICT below reading the omitted field's current row value first) - but each
     * field present must be numeric and non-negative, same validation shape
     * createEgressAllowlistEntry already uses for its own numeric fields.
     */
    public function updateAlertThresholds(): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireAdminRole()) !== null) {
            return $error;
        }

        $body = $this->request->getJsonRawBody(true) ?? [];
        $current = $this->runInCompanyTransaction(static fn ($db) => AlertThresholds::resolve($db));

        $values = $current;
        foreach (AlertThresholds::DEFAULTS as $key => $default) {
            if (!array_key_exists($key, $body)) {
                continue;
            }
            // Whether $default (the literal in AlertThresholds::DEFAULTS, e.g. 3 vs 80.0)
            // is a PHP int tells us whether the matching DB column is integer or numeric
            // - error_rate_min_errors/guardrail_min_count/latency_min_ms are integer
            // columns (see the Phinx migration), so a fractional value like 2.5 must be
            // rejected here with a clean 400, not reach Postgres and throw an uncaught
            // PDOException (invalid input syntax for type integer) that would otherwise
            // surface as a 500.
            $valid = is_int($default)
                ? is_int($body[$key]) && $body[$key] >= 0
                : is_numeric($body[$key]) && $body[$key] >= 0;
            if (!$valid) {
                return $this->jsonResponse(['error' => "invalid_{$key}"], 400);
            }
            $values[$key] = $body[$key];
        }

        $row = $this->runInCompanyTransaction(
            fn ($db) => $db->fetchOne(
                'INSERT INTO console.alert_thresholds (
                    company_id, quota_pct, error_rate_multiplier, error_rate_min_errors,
                    error_rate_min_pct, guardrail_multiplier, guardrail_min_count,
                    latency_multiplier, latency_min_ms, cost_multiplier, cost_min_usd
                 ) VALUES (
                    :company_id, :quota_pct, :error_rate_multiplier, :error_rate_min_errors,
                    :error_rate_min_pct, :guardrail_multiplier, :guardrail_min_count,
                    :latency_multiplier, :latency_min_ms, :cost_multiplier, :cost_min_usd
                 )
                 ON CONFLICT (company_id) DO UPDATE SET
                    quota_pct = EXCLUDED.quota_pct,
                    error_rate_multiplier = EXCLUDED.error_rate_multiplier,
                    error_rate_min_errors = EXCLUDED.error_rate_min_errors,
                    error_rate_min_pct = EXCLUDED.error_rate_min_pct,
                    guardrail_multiplier = EXCLUDED.guardrail_multiplier,
                    guardrail_min_count = EXCLUDED.guardrail_min_count,
                    latency_multiplier = EXCLUDED.latency_multiplier,
                    latency_min_ms = EXCLUDED.latency_min_ms,
                    cost_multiplier = EXCLUDED.cost_multiplier,
                    cost_min_usd = EXCLUDED.cost_min_usd,
                    updated_at = now()
                 RETURNING *',
                Enum::FETCH_ASSOC,
                [
                    'company_id' => $this->getCompanyId(),
                    'quota_pct' => $values['quota_pct'],
                    'error_rate_multiplier' => $values['error_rate_multiplier'],
                    'error_rate_min_errors' => $values['error_rate_min_errors'],
                    'error_rate_min_pct' => $values['error_rate_min_pct'],
                    'guardrail_multiplier' => $values['guardrail_multiplier'],
                    'guardrail_min_count' => $values['guardrail_min_count'],
                    'latency_multiplier' => $values['latency_multiplier'],
                    'latency_min_ms' => $values['latency_min_ms'],
                    'cost_multiplier' => $values['cost_multiplier'],
                    'cost_min_usd' => $values['cost_min_usd'],
                ]
            )
        );

        return $this->jsonResponse(AlertThresholds::format($row));
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
