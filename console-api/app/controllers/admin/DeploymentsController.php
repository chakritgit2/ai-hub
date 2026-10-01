<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Db\Enum;
use Phalcon\Http\Response;
use Throwable;

/**
 * /deployments — gateway-facing deployment configs (PRD §6.3, §8.1 `deployments` table).
 *
 * `promoteDeployment` stays 501 (x-phase: 3, see contracts/openapi/console-api.yaml).
 * Everything else is real.
 */
class DeploymentsController extends ControllerBase
{
    private const ENVIRONMENTS = ['staging', 'production'];
    private const OUTPUT_MODES = ['stream', 'buffered'];

    public function listDeployments(): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $rows = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchAll(
                'SELECT id, company_id, slug, environment, agent_version_id, rate_limit_per_min,
                        daily_token_limit, daily_cost_limit_usd, allowed_origins, guardrail_overrides,
                        output_mode, allow_write_tools, conversation_ttl_days, config_version, enabled,
                        created_at, updated_at
                 FROM console.deployments ORDER BY created_at DESC',
                Enum::FETCH_ASSOC
            )
        );

        return $this->jsonResponse(array_map([$this, 'formatDeployment'], $rows));
    }

    public function createDeployment(): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireWriteRole()) !== null) {
            return $error;
        }

        $body = $this->request->getJsonRawBody(true) ?? [];
        $slug = $body['slug'] ?? null;
        if (!is_string($slug) || trim($slug) === '') {
            return $this->jsonResponse(['error' => 'slug_required'], 400);
        }
        $agentVersionId = $body['agent_version_id'] ?? null;
        if (!is_string($agentVersionId) || !$this->isUuid($agentVersionId)) {
            return $this->jsonResponse(['error' => 'agent_version_id_required'], 400);
        }

        [$fields, $error] = $this->parseOptionalFields($body);
        if ($error !== null) {
            return $error;
        }

        if ($this->findPublishedAgentVersion($agentVersionId) === null) {
            return $this->jsonResponse(['error' => 'agent_version_not_published'], 400);
        }

        try {
            $row = $this->runInCompanyTransaction(
                fn ($db) => $db->fetchOne(
                    'INSERT INTO console.deployments
                        (company_id, slug, agent_version_id, environment, rate_limit_per_min,
                         daily_token_limit, daily_cost_limit_usd, allowed_origins, guardrail_overrides,
                         output_mode, allow_write_tools, conversation_ttl_days, enabled)
                     VALUES
                        (:company_id, :slug, :agent_version_id, :environment, :rate_limit_per_min,
                         :daily_token_limit, :daily_cost_limit_usd, :allowed_origins::jsonb, :guardrail_overrides::jsonb,
                         :output_mode, :allow_write_tools, :conversation_ttl_days, :enabled)
                     RETURNING id, company_id, slug, environment, agent_version_id, rate_limit_per_min,
                               daily_token_limit, daily_cost_limit_usd, allowed_origins, guardrail_overrides,
                               output_mode, allow_write_tools, conversation_ttl_days, config_version, enabled,
                               created_at, updated_at',
                    Enum::FETCH_ASSOC,
                    [
                        'company_id' => $this->getCompanyId(),
                        'slug' => $slug,
                        'agent_version_id' => $agentVersionId,
                        'environment' => $fields['environment'] ?? 'production',
                        'rate_limit_per_min' => $fields['rate_limit_per_min'] ?? 60,
                        'daily_token_limit' => $fields['daily_token_limit'] ?? null,
                        'daily_cost_limit_usd' => $fields['daily_cost_limit_usd'] ?? null,
                        'allowed_origins' => json_encode($fields['allowed_origins'] ?? []),
                        // json_encode([]) (PHP's empty-array default) produces the JSON
                        // array "[]", not the JSON object "{}" this jsonb column's own
                        // DEFAULT uses and ai-runtime expects (guardrail_overrides is a
                        // map of check overrides, never a list) - '{}' as a literal
                        // string sidesteps PHP's empty-array/empty-object ambiguity
                        // entirely. Confirmed live: ai-runtime's guardrail-merge logic
                        // crashed on an overrides value of [] with AttributeError.
                        'guardrail_overrides' => empty($fields['guardrail_overrides'])
                            ? '{}'
                            : json_encode($fields['guardrail_overrides']),
                        'output_mode' => $fields['output_mode'] ?? 'stream',
                        'allow_write_tools' => $this->boolParam($fields['allow_write_tools'] ?? false),
                        'conversation_ttl_days' => $fields['conversation_ttl_days'] ?? 30,
                        'enabled' => $this->boolParam($fields['enabled'] ?? true),
                    ]
                )
            );
        } catch (Throwable $exception) {
            if ($this->isUniqueViolation($exception)) {
                return $this->jsonResponse(['error' => 'slug_already_exists'], 409);
            }
            throw $exception;
        }

        return $this->jsonResponse($this->formatDeployment($row), 201);
    }

    public function getDeployment(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $row = $this->findDeployment($id);
        if ($row === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        return $this->jsonResponse($this->formatDeployment($row));
    }

    public function updateDeployment(string $id): Response
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
        $columns = [];

        if (array_key_exists('slug', $body)) {
            if (!is_string($body['slug']) || trim($body['slug']) === '') {
                return $this->jsonResponse(['error' => 'slug_required'], 400);
            }
            $columns['slug'] = $body['slug'];
        }
        if (array_key_exists('agent_version_id', $body)) {
            if (!is_string($body['agent_version_id']) || !$this->isUuid($body['agent_version_id'])) {
                return $this->jsonResponse(['error' => 'agent_version_id_required'], 400);
            }
            if ($this->findPublishedAgentVersion($body['agent_version_id']) === null) {
                return $this->jsonResponse(['error' => 'agent_version_not_published'], 400);
            }
            $columns['agent_version_id'] = $body['agent_version_id'];
        }

        [$fields, $error] = $this->parseOptionalFields($body);
        if ($error !== null) {
            return $error;
        }
        $jsonColumns = ['allowed_origins', 'guardrail_overrides'];
        $boolColumns = ['allow_write_tools', 'enabled'];
        foreach ($fields as $column => $value) {
            if ($column === 'guardrail_overrides') {
                // Same PHP empty-array/empty-object ambiguity as createDeployment's
                // default above - an explicit {} in the request body decodes to a PHP
                // [] indistinguishable from an explicit [], so re-encoding a genuinely
                // empty value must force the JSON object form ai-runtime expects.
                $columns[$column] = empty($value) ? '{}' : json_encode($value);
            } elseif (in_array($column, $jsonColumns, true)) {
                $columns[$column] = json_encode($value);
            } elseif (in_array($column, $boolColumns, true)) {
                $columns[$column] = $this->boolParam($value);
            } else {
                $columns[$column] = $value;
            }
        }

        try {
            $row = $this->runInCompanyTransaction(function ($db) use ($id, $columns, $jsonColumns) {
                if ($columns !== []) {
                    $params = ['id' => $id];
                    $setClauses = [];
                    foreach ($columns as $column => $value) {
                        $placeholder = in_array($column, $jsonColumns, true) ? ":{$column}::jsonb" : ":{$column}";
                        $setClauses[] = "{$column} = {$placeholder}";
                        $params[$column] = $value;
                    }
                    // Any field change here means the gateway's cached config for this
                    // deployment is stale — config_version is what it uses to notice.
                    $setClauses[] = 'config_version = config_version + 1';
                    $setClauses[] = 'updated_at = now()';
                    $db->execute(
                        'UPDATE console.deployments SET ' . implode(', ', $setClauses) . ' WHERE id = :id',
                        $params
                    );
                }

                return $db->fetchOne(
                    'SELECT id, company_id, slug, environment, agent_version_id, rate_limit_per_min,
                            daily_token_limit, daily_cost_limit_usd, allowed_origins, guardrail_overrides,
                            output_mode, allow_write_tools, conversation_ttl_days, config_version, enabled,
                            created_at, updated_at
                     FROM console.deployments WHERE id = :id',
                    Enum::FETCH_ASSOC,
                    ['id' => $id]
                );
            });
        } catch (Throwable $exception) {
            if ($this->isUniqueViolation($exception)) {
                return $this->jsonResponse(['error' => 'slug_already_exists'], 409);
            }
            throw $exception;
        }

        if (empty($row)) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        return $this->jsonResponse($this->formatDeployment($row));
    }

    public function deleteDeployment(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireWriteRole()) !== null) {
            return $error;
        }
        if ($this->findDeployment($id) === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        try {
            $this->runInCompanyTransaction(
                static fn ($db) => $db->execute('DELETE FROM console.deployments WHERE id = :id', ['id' => $id])
            );
        } catch (Throwable $exception) {
            // console.api_key_scopes.deployment_id references this row with no ON DELETE
            // clause (20260925000011_create_api_key_scopes_table.php), so a deployment
            // still scoped to an API key fails this DELETE with a FK violation rather
            // than cascading - surface that as a normal 409, not a raw 500.
            if ($this->isForeignKeyViolation($exception)) {
                return $this->jsonResponse(['error' => 'deployment_in_use'], 409);
            }
            throw $exception;
        }

        return $this->noContentResponse();
    }

    /** x-phase: 3 */
    public function promoteDeployment(string $id): Response
    {
        return $this->notImplemented();
    }

    private function isUniqueViolation(Throwable $exception): bool
    {
        return $exception instanceof \PDOException && ($exception->errorInfo[0] ?? null) === '23505';
    }

    private function isForeignKeyViolation(Throwable $exception): bool
    {
        return $exception instanceof \PDOException && ($exception->errorInfo[0] ?? null) === '23503';
    }

    /**
     * PDO's default (PARAM_STR) binding casts a PHP `false` to `''`, which Postgres
     * rejects for a `boolean` column ("invalid input syntax for type boolean"); `true`
     * happens to survive as `'1'`, so this only ever misfires on `false` — easy to miss
     * without a false-value test. The 'true'/'false' literal strings bind correctly for
     * both.
     */
    private function boolParam(bool $value): string
    {
        return $value ? 'true' : 'false';
    }

    /**
     * Parses the optional config fields shared by createDeployment/updateDeployment,
     * validating the two enum fields against the contract (no DB-level CHECK constraint
     * backs them). Returns [fields, null] on success or [[], Response] on the first
     * validation failure — createDeployment only uses present-with-defaults fields,
     * updateDeployment only applies what the caller actually sent (array_key_exists).
     *
     * @param array<string, mixed> $body
     * @return array{0: array<string, mixed>, 1: Response|null}
     */
    private function parseOptionalFields(array $body): array
    {
        $fields = [];

        if (array_key_exists('environment', $body)) {
            if (!is_string($body['environment']) || !in_array($body['environment'], self::ENVIRONMENTS, true)) {
                return [[], $this->jsonResponse(['error' => 'invalid_environment'], 400)];
            }
            $fields['environment'] = $body['environment'];
        }
        if (array_key_exists('rate_limit_per_min', $body)) {
            if (!is_int($body['rate_limit_per_min']) || $body['rate_limit_per_min'] < 1) {
                return [[], $this->jsonResponse(['error' => 'invalid_rate_limit_per_min'], 400)];
            }
            $fields['rate_limit_per_min'] = $body['rate_limit_per_min'];
        }
        if (array_key_exists('daily_token_limit', $body)) {
            if ($body['daily_token_limit'] !== null && !is_int($body['daily_token_limit'])) {
                return [[], $this->jsonResponse(['error' => 'invalid_daily_token_limit'], 400)];
            }
            $fields['daily_token_limit'] = $body['daily_token_limit'];
        }
        if (array_key_exists('daily_cost_limit_usd', $body)) {
            if ($body['daily_cost_limit_usd'] !== null && !is_numeric($body['daily_cost_limit_usd'])) {
                return [[], $this->jsonResponse(['error' => 'invalid_daily_cost_limit_usd'], 400)];
            }
            $fields['daily_cost_limit_usd'] = $body['daily_cost_limit_usd'];
        }
        if (array_key_exists('allowed_origins', $body)) {
            if (!is_array($body['allowed_origins']) || !$this->isStringList($body['allowed_origins'])) {
                return [[], $this->jsonResponse(['error' => 'invalid_allowed_origins'], 400)];
            }
            $fields['allowed_origins'] = $body['allowed_origins'];
        }
        if (array_key_exists('guardrail_overrides', $body)) {
            if (!is_array($body['guardrail_overrides'])) {
                return [[], $this->jsonResponse(['error' => 'invalid_guardrail_overrides'], 400)];
            }
            $fields['guardrail_overrides'] = $body['guardrail_overrides'];
        }
        if (array_key_exists('output_mode', $body)) {
            if (!is_string($body['output_mode']) || !in_array($body['output_mode'], self::OUTPUT_MODES, true)) {
                return [[], $this->jsonResponse(['error' => 'invalid_output_mode'], 400)];
            }
            $fields['output_mode'] = $body['output_mode'];
        }
        if (array_key_exists('allow_write_tools', $body)) {
            if (!is_bool($body['allow_write_tools'])) {
                return [[], $this->jsonResponse(['error' => 'invalid_allow_write_tools'], 400)];
            }
            $fields['allow_write_tools'] = $body['allow_write_tools'];
        }
        if (array_key_exists('conversation_ttl_days', $body)) {
            if (!is_int($body['conversation_ttl_days']) || $body['conversation_ttl_days'] < 1) {
                return [[], $this->jsonResponse(['error' => 'invalid_conversation_ttl_days'], 400)];
            }
            $fields['conversation_ttl_days'] = $body['conversation_ttl_days'];
        }
        if (array_key_exists('enabled', $body)) {
            if (!is_bool($body['enabled'])) {
                return [[], $this->jsonResponse(['error' => 'invalid_enabled'], 400)];
            }
            $fields['enabled'] = $body['enabled'];
        }

        return [$fields, null];
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
    private function findDeployment(string $id): ?array
    {
        if (!$this->isUuid($id)) {
            return null;
        }

        $row = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchOne(
                'SELECT id, company_id, slug, environment, agent_version_id, rate_limit_per_min,
                        daily_token_limit, daily_cost_limit_usd, allowed_origins, guardrail_overrides,
                        output_mode, allow_write_tools, conversation_ttl_days, config_version, enabled,
                        created_at, updated_at
                 FROM console.deployments WHERE id = :id',
                Enum::FETCH_ASSOC,
                ['id' => $id]
            )
        );

        return empty($row) ? null : $row;
    }

    /**
     * A deployment must point at an already-published version (PRD §6.3 "an endpoint
     * bound to a published agent version") — an unpublished/draft version has no
     * compiled_definition, so the gateway/ai-runtime would have nothing to run.
     * RLS (via runInCompanyTransaction) already confines this lookup to the current
     * company, so a published version belonging to another company is correctly
     * invisible here too.
     *
     * @return array<string, mixed>|null
     */
    private function findPublishedAgentVersion(string $agentVersionId): ?array
    {
        $row = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchOne(
                'SELECT id FROM console.agent_versions WHERE id = :id AND is_published = true',
                Enum::FETCH_ASSOC,
                ['id' => $agentVersionId]
            )
        );

        return empty($row) ? null : $row;
    }

    /**
     * @param array<string, mixed> $row
     * @return array<string, mixed>
     */
    private function formatDeployment(array $row): array
    {
        return [
            'id' => $row['id'],
            'company_id' => $row['company_id'],
            'slug' => $row['slug'],
            'environment' => $row['environment'],
            'agent_version_id' => $row['agent_version_id'],
            'rate_limit_per_min' => (int) $row['rate_limit_per_min'],
            'daily_token_limit' => $row['daily_token_limit'] !== null ? (int) $row['daily_token_limit'] : null,
            'daily_cost_limit_usd' => $row['daily_cost_limit_usd'] !== null ? (float) $row['daily_cost_limit_usd'] : null,
            'allowed_origins' => json_decode((string) $row['allowed_origins'], true),
            'guardrail_overrides' => json_decode((string) $row['guardrail_overrides'], true),
            'output_mode' => $row['output_mode'],
            'allow_write_tools' => (bool) $row['allow_write_tools'],
            'conversation_ttl_days' => (int) $row['conversation_ttl_days'],
            'config_version' => (int) $row['config_version'],
            'enabled' => (bool) $row['enabled'],
            'created_at' => $row['created_at'],
            'updated_at' => $row['updated_at'],
        ];
    }
}
