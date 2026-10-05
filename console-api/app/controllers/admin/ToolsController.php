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
 * /tools — company-scoped tool definitions (PRD §6.5, §8.1 `tools` table).
 *
 * This is CRUD + a standalone connectivity test only — a tool definition here is not yet
 * wired into the agent spec/compiler, so no agent can actually call one during a run.
 * `compiled_definition.agent.tools` stays hardcoded to `[]` in app/services/compiler.py until
 * that follow-up lands (see the plan this was built from for why: Dynamiq's own tool nodes
 * give no SafeHttpClient injection point on the path actually used at runtime, and
 * runtime.py's `_execute_agent_run` has no per-tool-call hook yet for approval/delegated
 * tokens either — both need their own dedicated design pass).
 */
class ToolsController extends ControllerBase
{
    private const KINDS = ['http', 'builtin', 'python'];
    private const ACCESS_LEVELS = ['read', 'write'];
    private const AUTH_MODES = ['service', 'delegated'];

    public function listTools(): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $rows = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchAll(
                'SELECT id, company_id, name, kind, access_level, auth_mode, audience, config,
                        enabled, created_at, updated_at
                 FROM console.tools ORDER BY created_at DESC',
                Enum::FETCH_ASSOC
            )
        );

        return $this->jsonResponse(array_map([$this, 'formatTool'], $rows));
    }

    public function createTool(): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }

        $body = $this->request->getJsonRawBody(true) ?? [];

        [$fields, $error] = $this->validateToolFields($body);
        if ($error !== null) {
            return $error;
        }
        $fields += ['name' => null, 'kind' => null, 'access_level' => 'read', 'auth_mode' => 'service',
            'audience' => null, 'config' => [], 'enabled' => true];

        if ($fields['name'] === null) {
            return $this->jsonResponse(['error' => 'name_required'], 400);
        }
        if ($fields['kind'] === null) {
            return $this->jsonResponse(['error' => 'kind_required'], 400);
        }

        if (($error = $this->requireWriteRoleForKind($fields['kind'])) !== null) {
            return $error;
        }
        if (($error = $this->assertToolUrlAllowed($fields['kind'], $fields['config'])) !== null) {
            return $error;
        }

        try {
            $row = $this->runInCompanyTransaction(
                fn ($db) => $db->fetchOne(
                    'INSERT INTO console.tools
                        (company_id, name, kind, access_level, auth_mode, audience, config, enabled)
                     VALUES (:company_id, :name, :kind, :access_level, :auth_mode, :audience, :config::jsonb, :enabled)
                     RETURNING id, company_id, name, kind, access_level, auth_mode, audience, config,
                               enabled, created_at, updated_at',
                    Enum::FETCH_ASSOC,
                    [
                        'company_id' => $this->getCompanyId(),
                        'name' => $fields['name'],
                        'kind' => $fields['kind'],
                        'access_level' => $fields['access_level'],
                        'auth_mode' => $fields['auth_mode'],
                        'audience' => $fields['audience'],
                        'config' => $this->encodeToolConfig($fields['config']),
                        'enabled' => $fields['enabled'] ? 'true' : 'false',
                    ]
                )
            );
        } catch (Throwable $exception) {
            if ($this->isUniqueViolation($exception)) {
                return $this->jsonResponse(['error' => 'name_already_exists'], 409);
            }
            throw $exception;
        }

        return $this->jsonResponse($this->formatTool($row), 201);
    }

    public function updateTool(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        // Membership (not yet write-role — that depends on the effective kind, decided
        // below) gates even looking the row up, matching the rest of this codebase's
        // "a non-member gets 404 before anything else runs" convention.
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $existing = $this->findTool($id);
        if ($existing === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        $body = $this->request->getJsonRawBody(true) ?? [];

        [$fields, $error] = $this->validateToolFields($body);
        if ($error !== null) {
            return $error;
        }

        $effectiveKind = $fields['kind'] ?? $existing['kind'];
        if (($error = $this->requireWriteRoleForKind($effectiveKind)) !== null) {
            return $error;
        }

        // Only re-validate the URL when config is actually part of this update — matches
        // ConnectionsController::updateConnection's "only check what's changing" convention.
        // Re-checking the existing (already-saved, already-valid) config on every unrelated
        // update (e.g. a bare {"enabled": false}) would 422 a tool whose URL used to be
        // allowlisted but no longer is, even though this request never touched it.
        if (array_key_exists('config', $fields)) {
            if (($error = $this->assertToolUrlAllowed($effectiveKind, $fields['config'])) !== null) {
                return $error;
            }
        }

        try {
            $row = $this->runInCompanyTransaction(function ($db) use ($id, $fields) {
                if ($fields !== []) {
                    $params = ['id' => $id];
                    $setClauses = [];
                    foreach ($fields as $column => $value) {
                        if ($column === 'config') {
                            $setClauses[] = 'config = :config::jsonb';
                            $params['config'] = $this->encodeToolConfig($value);
                            continue;
                        }
                        if ($column === 'enabled') {
                            $setClauses[] = 'enabled = :enabled';
                            $params['enabled'] = $value ? 'true' : 'false';
                            continue;
                        }
                        $setClauses[] = "{$column} = :{$column}";
                        $params[$column] = $value;
                    }
                    $setClauses[] = 'updated_at = now()';
                    $db->execute(
                        'UPDATE console.tools SET ' . implode(', ', $setClauses) . ' WHERE id = :id',
                        $params
                    );
                }

                return $db->fetchOne(
                    'SELECT id, company_id, name, kind, access_level, auth_mode, audience, config,
                            enabled, created_at, updated_at
                     FROM console.tools WHERE id = :id',
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

        return $this->jsonResponse($this->formatTool($row));
    }

    public function deleteTool(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $existing = $this->findTool($id);
        if ($existing === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }
        if (($error = $this->requireWriteRoleForKind($existing['kind'])) !== null) {
            return $error;
        }

        $this->runInCompanyTransaction(
            static fn ($db) => $db->execute('DELETE FROM console.tools WHERE id = :id', ['id' => $id])
        );

        return $this->noContentResponse();
    }

    public function testTool(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }
        if ($this->findTool($id) === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        /** @var RuntimeClient $runtimeClient */
        $runtimeClient = $this->getDI()->getShared('runtimeClient');

        try {
            $result = $runtimeClient->testTool($id);
        } catch (RuntimeException $exception) {
            return $this->jsonResponse(
                ['error' => 'test_request_failed', 'message' => $exception->getMessage()],
                502
            );
        }

        return $this->jsonResponse($result);
    }

    /**
     * Validates whatever fields are present in $body (all required on create via the
     * caller's own defaulting, all optional/partial on update). Returns [fields, null] on
     * success or [[], Response] on the first validation failure.
     *
     * @param array<string, mixed> $body
     * @return array{0: array<string, mixed>, 1: ?Response}
     */
    private function validateToolFields(array $body): array
    {
        $fields = [];

        if (array_key_exists('name', $body)) {
            if (!is_string($body['name']) || trim($body['name']) === '') {
                return [[], $this->jsonResponse(['error' => 'name_required'], 400)];
            }
            $fields['name'] = $body['name'];
        }

        if (array_key_exists('kind', $body)) {
            if (!is_string($body['kind']) || !in_array($body['kind'], self::KINDS, true)) {
                return [[], $this->jsonResponse(['error' => 'kind_invalid', 'allowed' => self::KINDS], 400)];
            }
            $fields['kind'] = $body['kind'];
        }

        if (array_key_exists('access_level', $body)) {
            if (!is_string($body['access_level']) || !in_array($body['access_level'], self::ACCESS_LEVELS, true)) {
                return [[], $this->jsonResponse(
                    ['error' => 'access_level_invalid', 'allowed' => self::ACCESS_LEVELS],
                    400
                )];
            }
            $fields['access_level'] = $body['access_level'];
        }

        if (array_key_exists('auth_mode', $body)) {
            if (!is_string($body['auth_mode']) || !in_array($body['auth_mode'], self::AUTH_MODES, true)) {
                return [[], $this->jsonResponse(['error' => 'auth_mode_invalid', 'allowed' => self::AUTH_MODES], 400)];
            }
            $fields['auth_mode'] = $body['auth_mode'];
        }

        if (array_key_exists('audience', $body)) {
            if ($body['audience'] !== null && !is_string($body['audience'])) {
                return [[], $this->jsonResponse(['error' => 'audience_must_be_string_or_null'], 400)];
            }
            $fields['audience'] = $body['audience'];
        }

        if (array_key_exists('config', $body)) {
            if (!is_array($body['config'])) {
                return [[], $this->jsonResponse(['error' => 'config_must_be_object'], 400)];
            }
            $fields['config'] = $body['config'];
        }

        if (array_key_exists('enabled', $body)) {
            if (!is_bool($body['enabled'])) {
                return [[], $this->jsonResponse(['error' => 'enabled_must_be_boolean'], 400)];
            }
            $fields['enabled'] = $body['enabled'];
        }

        return [$fields, null];
    }

    /**
     * `kind: python` is code-execution and admin-only (PRD §2 Non-goals); everything else
     * (http/builtin) only needs the usual developer-or-admin write role.
     */
    private function requireWriteRoleForKind(string $kind): ?Response
    {
        return $kind === 'python' ? $this->requireAdminRole() : $this->requireWriteRole();
    }

    /**
     * Save-time egress check for `kind: http` tools, reusing the exact same
     * EgressAllowlistMatcher/query pattern as ConnectionsController::assertApiBaseAllowed —
     * a tool's configured URL is just as much an egress vector as a connection's api_base
     * (PRD §7.4). `builtin`/`python` tools have no user-supplied URL to check.
     *
     * @param array<string, mixed> $config
     */
    private function assertToolUrlAllowed(string $kind, array $config): ?Response
    {
        if ($kind !== 'http') {
            return null;
        }
        $url = $config['url'] ?? null;
        if ($url === null) {
            return null;
        }
        if (!is_string($url)) {
            return $this->jsonResponse(['error' => 'config_url_must_be_string'], 400);
        }

        $host = parse_url($url, PHP_URL_HOST);
        if (!is_string($host) || $host === '') {
            return $this->jsonResponse(['error' => 'config_url_invalid'], 400);
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
     * `json_encode([])` is always the JSON array `'[]'`, never the object `'{}'` — PHP has
     * no way to tell an empty associative array from an empty list, and `config` must
     * always round-trip as a JSON *object* (`DEFAULT '{}'`, PRD §8.2) or Python's
     * `tool.config.get(...)` crashes on what it reads back as a list. Only the empty case
     * needs special-casing: `JSON_FORCE_OBJECT` would "fix" that but recursively turns
     * every nested array (e.g. a JSON-Schema `enum`/`required` list under `config`) into a
     * numeric-keyed object too, corrupting real HTTP-tool input schemas.
     *
     * @param array<string, mixed> $config
     */
    private function encodeToolConfig(array $config): string
    {
        return $config === [] ? '{}' : json_encode($config);
    }

    private function isUniqueViolation(Throwable $exception): bool
    {
        return $exception instanceof \PDOException && ($exception->errorInfo[0] ?? null) === '23505';
    }

    /**
     * @return array<string, mixed>|null
     */
    private function findTool(string $id): ?array
    {
        if (!$this->isUuid($id)) {
            return null;
        }

        $row = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchOne(
                'SELECT id, company_id, name, kind, access_level, auth_mode, audience, config,
                        enabled, created_at, updated_at
                 FROM console.tools WHERE id = :id',
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
    private function formatTool(array $row): array
    {
        return [
            'id' => $row['id'],
            'company_id' => $row['company_id'],
            'name' => $row['name'],
            'kind' => $row['kind'],
            'access_level' => $row['access_level'],
            'auth_mode' => $row['auth_mode'],
            'audience' => $row['audience'],
            'config' => json_decode((string) $row['config'], true),
            'enabled' => (bool) $row['enabled'],
            'created_at' => $row['created_at'],
            'updated_at' => $row['updated_at'],
        ];
    }
}
