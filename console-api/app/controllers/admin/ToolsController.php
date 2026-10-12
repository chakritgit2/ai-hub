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
 * CRUD + a standalone connectivity test + secret storage for `auth_mode: "service"`
 * tools. `kind: http` tools are wired into the agent spec/compiler for real
 * (`app/services/compiler.py`'s `_build_http_tool`/`_build_tools` build a real
 * `SafeHttpApiCall` node, egress-allowlist-checked via `SafeHttpClient`); `kind: builtin`
 * (Tavily/Exa web search) tools are likewise wired for real
 * (`_build_builtin_tool`/`SafeTavilyTool`/`SafeExaTool`), always `auth_mode: "service"`
 * (see `assertBuiltinToolValid`) and fail-closed at run time if no secret is stored
 * (`app.services.runtime`). `kind: python` still has no execution path (own backlog
 * item, needs its own sandboxing design).
 */
class ToolsController extends ControllerBase
{
    private const KINDS = ['http', 'builtin', 'python'];
    private const ACCESS_LEVELS = ['read', 'write'];
    private const AUTH_MODES = ['service', 'delegated'];
    private const BUILTIN_PROVIDERS = ['tavily', 'exa'];

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
                        masked_hint, enabled, created_at, updated_at
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
        // Membership (not yet write-role — that depends on `kind`, only known once the
        // body is validated below) gates even validating input, matching updateTool's/
        // deleteTool's "a non-member gets 404 before anything else runs" convention —
        // without this, a non-member could probe field-validation rules via a 400
        // instead of getting the 404 every other /tools action gives them.
        if (($error = $this->requireMembership()) !== null) {
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
        if (($error = $this->assertBuiltinToolValid($fields['kind'], $fields['auth_mode'], $fields['config'])) !== null) {
            return $error;
        }

        try {
            $row = $this->runInCompanyTransaction(
                fn ($db) => $db->fetchOne(
                    'INSERT INTO console.tools
                        (company_id, name, kind, access_level, auth_mode, audience, config, enabled)
                     VALUES (:company_id, :name, :kind, :access_level, :auth_mode, :audience, :config::jsonb, :enabled)
                     RETURNING id, company_id, name, kind, access_level, auth_mode, audience, config,
                               masked_hint, enabled, created_at, updated_at',
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
        // Unlike assertToolUrlAllowed, this is a pure in-memory check (no DB round-trip),
        // so there's no cost reason to skip it just because kind/auth_mode/config weren't
        // part of this particular update - always re-check the effective state.
        $effectiveAuthMode = $fields['auth_mode'] ?? $existing['auth_mode'];
        $effectiveConfig = $fields['config'] ?? json_decode((string) $existing['config'], true);
        if (($error = $this->assertBuiltinToolValid($effectiveKind, $effectiveAuthMode, $effectiveConfig)) !== null) {
            return $error;
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
                            masked_hint, enabled, created_at, updated_at
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

        /** @var RuntimeClient $runtimeClient */
        $runtimeClient = $this->getDI()->getShared('runtimeClient');
        try {
            $runtimeClient->deleteToolSecret($id);
        } catch (RuntimeException $exception) {
            // Best-effort from here on: the tool is already gone from the catalog (the
            // authoritative delete above succeeded), so the user-visible operation is
            // done - a leftover runtime.tool_secrets row is an ops cleanup concern, not a
            // reason to report failure for an already-completed delete. Same "log, don't
            // fail the request" convention as KbController::deleteKnowledgeBase.
            error_log("ToolsController::deleteTool: secret cleanup failed for {$id}: " . $exception->getMessage());
        }

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
        $existing = $this->findTool($id);
        if ($existing === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }
        // Unlike listTools, this makes a real outbound HTTP request to the tool's
        // configured URL (via ai-runtime/SafeHttpClient) — the same write-capability bar
        // as actually running the tool, not just reading its definition. Matches
        // ConnectionsController::testConnection's requireWriteRole(), kind-aware per this
        // controller's own create/update/delete convention.
        if (($error = $this->requireWriteRoleForKind($existing['kind'])) !== null) {
            return $error;
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
     * Validates length, forwards to ai-runtime (outside any DB transaction — never hold
     * one open across an HTTP call), then records only a masked hint on success — the
     * `auth_mode: "service"` counterpart to ConnectionsController::putConnectionSecret().
     * The plaintext secret never touches console-api's own storage. Callable for any
     * kind/auth_mode (not just `http`/`service`) — the web UI only surfaces the control
     * when it's meaningful, same as how a `python` tool's config is still editable here
     * even though nothing executes it yet.
     */
    public function putToolSecret(string $id): Response
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

        $body = $this->request->getJsonRawBody(true) ?? [];
        $secret = $body['secret'] ?? null;
        if (!is_string($secret) || strlen($secret) < 8) {
            return $this->jsonResponse(['error' => 'secret_too_short'], 400);
        }

        /** @var RuntimeClient $runtimeClient */
        $runtimeClient = $this->getDI()->getShared('runtimeClient');

        try {
            $runtimeClient->putToolSecret($id, $secret);
        } catch (RuntimeException $exception) {
            return $this->jsonResponse(
                ['error' => 'secret_store_failed', 'message' => $exception->getMessage()],
                502
            );
        }

        $maskedHint = '••••' . substr($secret, -4);
        $this->runInCompanyTransaction(
            static fn ($db) => $db->execute(
                'UPDATE console.tools SET masked_hint = :masked_hint, updated_at = now() WHERE id = :id',
                ['masked_hint' => $maskedHint, 'id' => $id]
            )
        );

        return $this->noContentResponse();
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
        $port = parse_url($url, PHP_URL_PORT) ?? EgressAllowlistMatcher::defaultPortForScheme(
            parse_url($url, PHP_URL_SCHEME)
        );

        $rows = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchAll(
                'SELECT host_pattern, port, allow_private_ip FROM console.egress_allowlist',
                Enum::FETCH_ASSOC
            )
        );

        if (!EgressAllowlistMatcher::hostAllowed($rows, $host, $port)) {
            return $this->jsonResponse(['error' => 'egress_host_not_allowed', 'host' => $host], 422);
        }

        return null;
    }

    /**
     * Save-time validation for `kind: builtin` (Tavily/Exa web search, PRD §6.5) tools —
     * a no-op for every other kind. `config.provider` must name a known provider (there's
     * no user-supplied URL/connection to check here, unlike `kind: http`). `auth_mode`
     * must be `"service"` — there's no end-user identity to delegate to a third-party
     * search API, and `app.services.runtime`'s fail-closed secret resolution for builtin
     * tools assumes `auth_mode: "service"` is the only case it ever sees.
     *
     * @param array<string, mixed> $config
     */
    private function assertBuiltinToolValid(string $kind, string $authMode, array $config): ?Response
    {
        if ($kind !== 'builtin') {
            return null;
        }
        $provider = $config['provider'] ?? null;
        if (!is_string($provider) || !in_array($provider, self::BUILTIN_PROVIDERS, true)) {
            return $this->jsonResponse(
                ['error' => 'config_provider_invalid', 'allowed' => self::BUILTIN_PROVIDERS],
                400
            );
        }
        if ($authMode !== 'service') {
            return $this->jsonResponse(['error' => 'builtin_tools_require_service_auth_mode'], 400);
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
                        masked_hint, enabled, created_at, updated_at
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
            'masked_hint' => $row['masked_hint'],
            'enabled' => (bool) $row['enabled'],
            'created_at' => $row['created_at'],
            'updated_at' => $row['updated_at'],
        ];
    }
}
