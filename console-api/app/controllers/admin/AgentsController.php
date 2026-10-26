<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use ConsoleApi\Services\RuntimeClient;
use Phalcon\Db\Adapter\Pdo\AbstractPdo;
use Phalcon\Db\Enum;
use Phalcon\Http\Response;
use Symfony\Component\Yaml\Exception\ParseException;
use Symfony\Component\Yaml\Yaml;
use Throwable;

/**
 * /agents and /agents/{id}/versions* — agent specs and their published versions
 * (PRD §4.4-A, §8.1 `agents` + `agent_versions` tables).
 */
class AgentsController extends ControllerBase
{
    // An agent spec (identity/model/guardrails/tools/skills/knowledge refs/memory/
    // publish_gate) is a few KB at most - well under SkillsController's 100 KB raw-text
    // cap. This is deliberately generous headroom over that, not a precise PRD figure.
    private const MAX_IMPORT_YAML_BYTES = 262144; // 256 KB

    /**
     * Pilot of "each menu shows only its own tenant's data" (option A, user-confirmed
     * scope): an optional X-Tenant-Id header, sent only by the Agents list page so far,
     * pools agents across every company under that tenant the caller is actually a
     * member of - instead of the usual single X-Company-Id-scoped company. Deliberately
     * the only endpoint that understands this header; every other list/detail endpoint
     * is untouched and still requires X-Company-Id as before. No RLS/security-boundary
     * change: this loops the exact same per-company RLS-scoped query once per company
     * instead of querying across companies in one go, so a user can never see an agent
     * belonging to a company they aren't a real member of, even within the right tenant.
     */
    public function listAgents(): Response
    {
        $tenantId = $_SERVER['HTTP_X_TENANT_ID'] ?? null;
        if ($tenantId !== null && $tenantId !== '') {
            return $this->listAgentsForTenant($tenantId);
        }

        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $rows = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchAll(
                'SELECT id, company_id, name, description, status, archived_at, created_at, updated_at
                 FROM console.agents ORDER BY created_at DESC',
                Enum::FETCH_ASSOC
            )
        );

        return $this->jsonResponse(array_map([$this, 'formatAgent'], $rows));
    }

    /**
     * $authUser['companies'] (from AuthService::syncUser(), PRD §7.1) is the same real
     * membership list requireMembership() already trusts - filtering it to this tenant
     * and looping is the only new logic here, not a new source of truth for access.
     * A tenant the caller has zero membership in (wrong tenant_id, or one they were
     * never added to) just yields an empty company list and therefore an empty (200)
     * result, same as any other company with no agents - no separate 404 needed, and no
     * information is leaked either way.
     */
    private function listAgentsForTenant(string $tenantId): Response
    {
        $authUser = $this->getAuthUser();
        $companyIds = array_values(array_unique(array_map(
            static fn (array $membership): string => $membership['company_id'],
            array_filter(
                $authUser['companies'] ?? [],
                static fn (array $membership): bool => $membership['tenant_id'] === $tenantId
            )
        )));

        $rows = [];
        foreach ($companyIds as $companyId) {
            $companyRows = $this->runInTransactionAsCompany(
                $companyId,
                static fn ($db) => $db->fetchAll(
                    'SELECT id, company_id, name, description, status, archived_at, created_at, updated_at
                     FROM console.agents ORDER BY created_at DESC',
                    Enum::FETCH_ASSOC
                )
            );
            array_push($rows, ...$companyRows);
        }

        // Each company's own rows arrive newest-first, but concatenating them in
        // company-iteration order doesn't make the merged set newest-first overall -
        // re-sort once across the whole pooled result.
        usort($rows, static fn (array $a, array $b): int => $b['created_at'] <=> $a['created_at']);

        return $this->jsonResponse(array_map([$this, 'formatAgent'], $rows));
    }

    public function createAgent(): Response
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
        $description = is_string($body['description'] ?? null) ? $body['description'] : null;

        try {
            $row = $this->runInCompanyTransaction(
                fn ($db) => $db->fetchOne(
                    'INSERT INTO console.agents (company_id, name, description)
                     VALUES (:company_id, :name, :description)
                     RETURNING id, company_id, name, description, status, archived_at, created_at, updated_at',
                    Enum::FETCH_ASSOC,
                    ['company_id' => $this->getCompanyId(), 'name' => $name, 'description' => $description]
                )
            );
        } catch (Throwable $exception) {
            if ($this->isUniqueViolation($exception)) {
                return $this->jsonResponse(['error' => 'name_already_exists'], 409);
            }
            throw $exception;
        }

        return $this->jsonResponse($this->formatAgent($row), 201);
    }

    public function getAgent(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $row = $this->findAgent($id);
        if ($row === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        return $this->jsonResponse($this->formatAgent($row));
    }

    public function updateAgent(string $id): Response
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
        if (array_key_exists('description', $body)) {
            $fields['description'] = is_string($body['description']) ? $body['description'] : null;
        }

        try {
            $row = $this->runInCompanyTransaction(function ($db) use ($id, $fields) {
                if ($fields !== []) {
                    $setClauses = array_map(static fn (string $column): string => "{$column} = :{$column}", array_keys($fields));
                    $setClauses[] = 'updated_at = now()';
                    $db->execute(
                        'UPDATE console.agents SET ' . implode(', ', $setClauses) . ' WHERE id = :id',
                        [...$fields, 'id' => $id]
                    );
                }

                return $db->fetchOne(
                    'SELECT id, company_id, name, description, status, archived_at, created_at, updated_at
                     FROM console.agents WHERE id = :id',
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

        return $this->jsonResponse($this->formatAgent($row));
    }

    public function cloneAgent(string $id): Response
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

        try {
            $cloned = $this->runInCompanyTransaction(function ($db) use ($id) {
                $source = $db->fetchOne(
                    'SELECT name, description FROM console.agents WHERE id = :id',
                    Enum::FETCH_ASSOC,
                    ['id' => $id]
                );
                if (empty($source)) {
                    return null;
                }

                $companyId = $this->getCompanyId();
                $newAgent = $db->fetchOne(
                    'INSERT INTO console.agents (company_id, name, description)
                     VALUES (:company_id, :name, :description)
                     RETURNING id, company_id, name, description, status, archived_at, created_at, updated_at',
                    Enum::FETCH_ASSOC,
                    [
                        'company_id' => $companyId,
                        'name' => $source['name'] . ' (copy)',
                        'description' => $source['description'],
                    ]
                );

                $latestVersion = $db->fetchOne(
                    'SELECT spec, spec_version FROM console.agent_versions
                     WHERE agent_id = :agent_id ORDER BY version_no DESC LIMIT 1',
                    Enum::FETCH_ASSOC,
                    ['agent_id' => $id]
                );

                if (!empty($latestVersion)) {
                    $db->execute(
                        'INSERT INTO console.agent_versions (company_id, agent_id, version_no, spec, spec_version)
                         VALUES (:company_id, :agent_id, 1, :spec::jsonb, :spec_version)',
                        [
                            'company_id' => $companyId,
                            'agent_id' => $newAgent['id'],
                            'spec' => $latestVersion['spec'],
                            'spec_version' => $latestVersion['spec_version'],
                        ]
                    );
                }

                return $newAgent;
            });
        } catch (Throwable $exception) {
            if ($this->isUniqueViolation($exception)) {
                return $this->jsonResponse(['error' => 'name_already_exists'], 409);
            }
            throw $exception;
        }

        if ($cloned === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        return $this->jsonResponse($this->formatAgent($cloned), 201);
    }

    /**
     * Soft-deletes an agent (PRD: no hard delete — agent_versions/deployments/runs
     * reference it, and `archived_at` already exists on this table for exactly this).
     *
     * Existence/state check and the UPDATE run inside one transaction, the UPDATE's own
     * WHERE re-checks `archived_at IS NULL`, and the result is read off `affectedRows()`
     * - same race-safety pattern as publishAgentVersion's `AND is_published = false` -
     * so two concurrent archive calls can't both report success.
     */
    public function archiveAgent(string $id): Response
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

        $result = $this->runInCompanyTransaction(function ($db) use ($id) {
            $existing = $db->fetchOne(
                'SELECT id FROM console.agents WHERE id = :id',
                Enum::FETCH_ASSOC,
                ['id' => $id]
            );
            if (empty($existing)) {
                return ['outcome' => 'not_found'];
            }

            $db->execute(
                "UPDATE console.agents SET status = 'archived', archived_at = now(), updated_at = now()
                 WHERE id = :id AND archived_at IS NULL",
                ['id' => $id]
            );
            if ($db->affectedRows() === 0) {
                return ['outcome' => 'already_archived'];
            }

            return ['outcome' => 'ok', 'row' => $db->fetchOne(
                'SELECT id, company_id, name, description, status, archived_at, created_at, updated_at
                 FROM console.agents WHERE id = :id',
                Enum::FETCH_ASSOC,
                ['id' => $id]
            )];
        });

        if ($result['outcome'] === 'not_found') {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }
        if ($result['outcome'] === 'already_archived') {
            return $this->jsonResponse(['error' => 'already_archived'], 409);
        }

        return $this->jsonResponse($this->formatAgent($result['row']));
    }

    /** Reverses archiveAgent - same single-transaction, WHERE-guarded, affectedRows() pattern. */
    public function unarchiveAgent(string $id): Response
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

        $result = $this->runInCompanyTransaction(function ($db) use ($id) {
            $existing = $db->fetchOne(
                'SELECT id FROM console.agents WHERE id = :id',
                Enum::FETCH_ASSOC,
                ['id' => $id]
            );
            if (empty($existing)) {
                return ['outcome' => 'not_found'];
            }

            $db->execute(
                "UPDATE console.agents SET status = 'draft', archived_at = null, updated_at = now()
                 WHERE id = :id AND archived_at IS NOT NULL",
                ['id' => $id]
            );
            if ($db->affectedRows() === 0) {
                return ['outcome' => 'not_archived'];
            }

            return ['outcome' => 'ok', 'row' => $db->fetchOne(
                'SELECT id, company_id, name, description, status, archived_at, created_at, updated_at
                 FROM console.agents WHERE id = :id',
                Enum::FETCH_ASSOC,
                ['id' => $id]
            )];
        });

        if ($result['outcome'] === 'not_found') {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }
        if ($result['outcome'] === 'not_archived') {
            return $this->jsonResponse(['error' => 'not_archived'], 409);
        }

        return $this->jsonResponse($this->formatAgent($result['row']));
    }

    /**
     * YAML snapshot of this agent's latest version - `name`/`description` (console.agents)
     * plus `spec`/`spec_version` (the latest console.agent_versions row, same "only the
     * latest version" scope cloneAgent already uses). Deliberately excludes
     * `compiled_definition` (resolved connection/tool details, not portable across
     * companies anyway) and every version's history (importAgent only ever creates one new
     * version, same as a normal createAgentVersion call).
     */
    public function exportAgent(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $agent = $this->findAgent($id);
        if ($agent === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        $latestVersion = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchOne(
                'SELECT spec, spec_version FROM console.agent_versions
                 WHERE agent_id = :agent_id ORDER BY version_no DESC LIMIT 1',
                Enum::FETCH_ASSOC,
                ['agent_id' => $id]
            )
        );
        if (empty($latestVersion)) {
            return $this->jsonResponse(['error' => 'no_versions'], 400);
        }

        $document = [
            'name' => $agent['name'],
            'description' => $agent['description'],
            'spec_version' => $latestVersion['spec_version'],
            'spec' => json_decode((string) $latestVersion['spec'], true),
        ];

        $filename = $this->escapeContentDispositionFilename($agent['name'] . '.yaml');

        $response = new Response();
        $response->setStatusCode(200);
        $response->setContentType('application/yaml');
        $response->setHeader('Content-Disposition', 'attachment; filename="' . $filename . '"');
        $response->setContent(Yaml::dump($document, 10, 2));

        return $response;
    }

    /**
     * Imports a YAML document (as produced by exportAgent, or hand-written in the same
     * shape) as a new draft version of the *existing* agent $id - mirrors
     * createAgentVersion exactly, just reading `spec`/`spec_version` from a parsed YAML
     * body instead of JSON. `name`/`description` in the document are accepted (for
     * round-tripping exportAgent's own output) but deliberately not applied to the target
     * agent - importing a spec into agent $id should never silently rename it.
     */
    public function importAgent(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireWriteRole()) !== null) {
            return $error;
        }

        $agent = $this->findAgent($id);
        if ($agent === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }
        if ($agent['archived_at'] !== null) {
            return $this->jsonResponse(['error' => 'agent_archived'], 409);
        }

        $raw = (string) $this->request->getRawBody();
        if (trim($raw) === '') {
            return $this->jsonResponse(['error' => 'yaml_required'], 400);
        }
        if (strlen($raw) > self::MAX_IMPORT_YAML_BYTES) {
            return $this->jsonResponse(['error' => 'yaml_too_large', 'max_bytes' => self::MAX_IMPORT_YAML_BYTES], 413);
        }

        try {
            // PARSE_EXCEPTION_ON_ALIAS rejects YAML anchors/aliases (`&foo`/`*foo`) outright
            // - without it, a ~500-byte "billion laughs" style payload (a chain of aliases
            // each referencing the previous one twice) parses near-instantly via PHP's
            // copy-on-write arrays, then exhausts all available memory the moment
            // insertNextAgentVersion()'s json_encode() below forces it to materialize -
            // an uncatchable fatal error, not something this try/catch can turn into a 400.
            // exportAgent never emits aliases and no legitimate agent spec needs them, so
            // this closes the hole for free.
            $document = Yaml::parse($raw, Yaml::PARSE_EXCEPTION_ON_ALIAS);
        } catch (ParseException $exception) {
            return $this->jsonResponse(['error' => 'invalid_yaml', 'message' => $exception->getMessage()], 400);
        }

        if (!is_array($document) || !is_array($document['spec'] ?? null)) {
            return $this->jsonResponse(['error' => 'spec_required'], 400);
        }
        $spec = $document['spec'];
        $specVersion = is_string($document['spec_version'] ?? null) && $document['spec_version'] !== ''
            ? $document['spec_version']
            : '1';

        try {
            $this->runInCompanyTransaction(function ($db) use ($id, $spec, $specVersion) {
                $this->insertNextAgentVersion($db, $this->getCompanyId(), $id, $spec, $specVersion);
            });
        } catch (Throwable $exception) {
            // Same race as createAgentVersion - two concurrent imports/creates for the same
            // agent can compute the same next_version_no.
            if ($this->isUniqueViolation($exception)) {
                return $this->jsonResponse(['error' => 'version_conflict_retry'], 409);
            }
            throw $exception;
        }

        return $this->jsonResponse($this->formatAgent($agent));
    }

    public function listAgentVersions(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }
        if ($this->findAgent($id) === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        $rows = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchAll(
                'SELECT id, company_id, agent_id, version_no, spec, spec_version, compiled_definition,
                        compiler_version, dynamiq_version, is_published, published_by, published_at,
                        created_at, updated_at
                 FROM console.agent_versions WHERE agent_id = :agent_id ORDER BY version_no DESC',
                Enum::FETCH_ASSOC,
                ['agent_id' => $id]
            )
        );

        return $this->jsonResponse(array_map([$this, 'formatAgentVersion'], $rows));
    }

    public function createAgentVersion(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireWriteRole()) !== null) {
            return $error;
        }
        $agent = $this->findAgent($id);
        if ($agent === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }
        if ($agent['archived_at'] !== null) {
            return $this->jsonResponse(['error' => 'agent_archived'], 409);
        }

        $body = $this->request->getJsonRawBody(true) ?? [];
        $spec = $body['spec'] ?? null;
        if (!is_array($spec)) {
            return $this->jsonResponse(['error' => 'spec_required'], 400);
        }
        $specVersion = is_string($body['spec_version'] ?? null) && $body['spec_version'] !== '' ? $body['spec_version'] : '1';

        try {
            $row = $this->runInCompanyTransaction(
                fn ($db) => $this->insertNextAgentVersion($db, $this->getCompanyId(), $id, $spec, $specVersion)
            );
        } catch (Throwable $exception) {
            // console.agent_versions has UNIQUE (agent_id, version_no) — two concurrent
            // creates for the same agent can compute the same next_version_no and race.
            if ($this->isUniqueViolation($exception)) {
                return $this->jsonResponse(['error' => 'version_conflict_retry'], 409);
            }
            throw $exception;
        }

        return $this->jsonResponse($this->formatAgentVersion($row), 201);
    }

    public function getAgentVersion(string $id, string $vid): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $row = $this->findAgentVersion($id, $vid);
        if ($row === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        return $this->jsonResponse($this->formatAgentVersion($row));
    }

    public function updateAgentVersion(string $id, string $vid): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireWriteRole()) !== null) {
            return $error;
        }

        $agent = $this->findAgent($id);
        if ($agent === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }
        if ($agent['archived_at'] !== null) {
            return $this->jsonResponse(['error' => 'agent_archived'], 409);
        }

        $existing = $this->findAgentVersion($id, $vid);
        if ($existing === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }
        if ((bool) $existing['is_published']) {
            // Publish makes a version immutable (PRD §6.1) — edit a new draft version instead.
            return $this->jsonResponse(['error' => 'version_is_published'], 409);
        }

        $body = $this->request->getJsonRawBody(true) ?? [];
        $fields = [];
        if (array_key_exists('spec', $body)) {
            if (!is_array($body['spec'])) {
                return $this->jsonResponse(['error' => 'spec_required'], 400);
            }
            $fields['spec'] = json_encode($body['spec']);
        }
        if (array_key_exists('spec_version', $body)) {
            if (!is_string($body['spec_version']) || $body['spec_version'] === '') {
                return $this->jsonResponse(['error' => 'spec_version_required'], 400);
            }
            $fields['spec_version'] = $body['spec_version'];
        }

        $row = $this->runInCompanyTransaction(function ($db) use ($vid, $fields) {
            if ($fields !== []) {
                $setClauses = [];
                // Still guarded by is_published = false, same reasoning as
                // publishAgentVersion's UPDATE — a concurrent publish between the check
                // above and this write shouldn't silently overwrite the now-immutable row.
                $params = ['id' => $vid];
                foreach ($fields as $column => $value) {
                    $placeholder = $column === 'spec' ? ":{$column}::jsonb" : ":{$column}";
                    $setClauses[] = "{$column} = {$placeholder}";
                    $params[$column] = $value;
                }
                $setClauses[] = 'updated_at = now()';
                $db->execute(
                    'UPDATE console.agent_versions SET ' . implode(', ', $setClauses)
                    . ' WHERE id = :id AND is_published = false',
                    $params
                );
            }

            return $db->fetchOne(
                'SELECT id, company_id, agent_id, version_no, spec, spec_version, compiled_definition,
                        compiler_version, dynamiq_version, is_published, published_by, published_at,
                        created_at, updated_at
                 FROM console.agent_versions WHERE id = :id',
                Enum::FETCH_ASSOC,
                ['id' => $vid]
            );
        });

        if ((bool) $row['is_published']) {
            // Lost the race against a concurrent publish between our check and our write.
            return $this->jsonResponse(['error' => 'version_is_published'], 409);
        }

        return $this->jsonResponse($this->formatAgentVersion($row));
    }

    /**
     * Recompiles via ai-runtime (RuntimeClient::compileAgentSpec), sets is_published=true,
     * bumps dependent deployments' config_version (PRD §4.4-A). A compile failure (bad
     * spec, cross-company connection, disallowed node type) leaves the version untouched
     * and is surfaced as 422 with the compiler's errors — publishing is all-or-nothing.
     */
    public function publishAgentVersion(string $id, string $vid): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }

        $role = $this->getRoleForCurrentCompany();
        if ($role === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }
        if (($error = $this->requireWriteRole()) !== null) {
            return $error;
        }

        $agent = $this->findAgent($id);
        if ($agent === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }
        if ($agent['archived_at'] !== null) {
            return $this->jsonResponse(['error' => 'agent_archived'], 409);
        }

        $existing = $this->findAgentVersion($id, $vid);
        if ($existing === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }
        if ((bool) $existing['is_published']) {
            return $this->jsonResponse(['error' => 'already_published'], 409);
        }

        $spec = json_decode($existing['spec'], true);

        /** @var RuntimeClient $runtimeClient */
        $runtimeClient = $this->getDI()->getShared('runtimeClient');

        try {
            $compileResult = $runtimeClient->compileAgentSpec($spec, $role);
        } catch (Throwable $exception) {
            return $this->jsonResponse(
                ['error' => 'compile_request_failed', 'message' => $exception->getMessage()],
                502
            );
        }

        if (!($compileResult['ok'] ?? false)) {
            return $this->jsonResponse(
                ['error' => 'compile_failed', 'errors' => $compileResult['errors'] ?? []],
                422
            );
        }

        // Publish gate (PRD §6.7) - opt-in per agent version (`spec.publish_gate.
        // enabled`, defaults false - every agent that never touches this tab publishes
        // exactly as before). Pydantic's own PublishGateSpec validator (ai/app/services/
        // agent_spec.py), already run by compileAgentSpec() above, guarantees an enabled
        // gate has both `metric` and `threshold` by this point - no need to re-check
        // their presence/shape here, only whether the actual score clears the bar. No
        // admin override: the user explicitly chose a hard block, no bypass.
        $gate = $spec['publish_gate'] ?? null;
        if (($gate['enabled'] ?? false) === true) {
            $gateError = $this->checkPublishGate($vid, $gate);
            if ($gateError !== null) {
                return $gateError;
            }
        }

        $userId = $this->getAuthUser()['id'];

        $result = $this->runInCompanyTransaction(function ($db) use ($vid, $compileResult, $userId) {
            // `AND is_published = false` closes the race between the check above and this
            // write: if a concurrent request published this version in between, this
            // UPDATE matches zero rows instead of silently overwriting the other request's
            // result (affectedRows() lets the caller tell the two cases apart).
            $db->execute(
                'UPDATE console.agent_versions
                 SET compiled_definition = :compiled_definition::jsonb,
                     compiler_version = :compiler_version,
                     dynamiq_version = :dynamiq_version,
                     is_published = true,
                     published_by = :published_by,
                     published_at = now(),
                     updated_at = now()
                 WHERE id = :id AND is_published = false',
                [
                    'compiled_definition' => json_encode($compileResult['compiled_definition']),
                    'compiler_version' => $compileResult['compiler_version'],
                    'dynamiq_version' => $compileResult['dynamiq_version'],
                    'published_by' => $userId,
                    'id' => $vid,
                ]
            );
            $updated = $db->affectedRows() > 0;

            if ($updated) {
                // Bumps config_version on every deployment bound to this agent version, so
                // the gateway notices its cached compiled_definition is stale (PRD §4.4-A).
                $db->execute(
                    'UPDATE console.deployments SET config_version = config_version + 1
                     WHERE agent_version_id = :agent_version_id',
                    ['agent_version_id' => $vid]
                );
            }

            $row = $db->fetchOne(
                'SELECT id, company_id, agent_id, version_no, spec, spec_version, compiled_definition,
                        compiler_version, dynamiq_version, is_published, published_by, published_at,
                        created_at, updated_at
                 FROM console.agent_versions WHERE id = :id',
                Enum::FETCH_ASSOC,
                ['id' => $vid]
            );

            return ['updated' => $updated, 'row' => $row];
        });

        if (!$result['updated']) {
            // Lost the race against a concurrent publish of this same version.
            return $this->jsonResponse(['error' => 'already_published'], 409);
        }

        return $this->jsonResponse($this->formatAgentVersion($result['row']));
    }

    private function isUniqueViolation(Throwable $exception): bool
    {
        return $exception instanceof \PDOException && ($exception->errorInfo[0] ?? null) === '23505';
    }

    /**
     * Inserts the next version_no row for $agentId within an already-open company
     * transaction - shared by createAgentVersion and importAgent (YAML import is just
     * "create a new version", with the spec coming from a parsed YAML body instead of a
     * JSON one).
     *
     * @param array<string, mixed> $spec
     * @return array<string, mixed>
     */
    private function insertNextAgentVersion(AbstractPdo $db, string $companyId, string $agentId, array $spec, string $specVersion): array
    {
        $next = $db->fetchOne(
            'SELECT COALESCE(MAX(version_no), 0) + 1 AS next_version_no
             FROM console.agent_versions WHERE agent_id = :agent_id',
            Enum::FETCH_ASSOC,
            ['agent_id' => $agentId]
        );

        return $db->fetchOne(
            'INSERT INTO console.agent_versions (company_id, agent_id, version_no, spec, spec_version)
             VALUES (:company_id, :agent_id, :version_no, :spec::jsonb, :spec_version)
             RETURNING id, company_id, agent_id, version_no, spec, spec_version, compiled_definition,
                       compiler_version, dynamiq_version, is_published, published_by, published_at,
                       created_at, updated_at',
            Enum::FETCH_ASSOC,
            [
                'company_id' => $companyId,
                'agent_id' => $agentId,
                'version_no' => $next['next_version_no'],
                'spec' => json_encode($spec),
                'spec_version' => $specVersion,
            ]
        );
    }

    /**
     * @return array<string, mixed>|null
     */
    private function findAgent(string $id): ?array
    {
        if (!$this->isUuid($id)) {
            return null;
        }

        $row = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchOne(
                'SELECT id, company_id, name, description, status, archived_at, created_at, updated_at
                 FROM console.agents WHERE id = :id',
                Enum::FETCH_ASSOC,
                ['id' => $id]
            )
        );

        return empty($row) ? null : $row;
    }

    /**
     * @return array<string, mixed>|null
     */
    private function findAgentVersion(string $id, string $vid): ?array
    {
        if (!$this->isUuid($id) || !$this->isUuid($vid)) {
            return null;
        }

        $row = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchOne(
                'SELECT id, company_id, agent_id, version_no, spec, spec_version, compiled_definition,
                        compiler_version, dynamiq_version, is_published, published_by, published_at,
                        created_at, updated_at
                 FROM console.agent_versions WHERE id = :vid AND agent_id = :id',
                Enum::FETCH_ASSOC,
                ['vid' => $vid, 'id' => $id]
            )
        );

        return empty($row) ? null : $row;
    }

    /**
     * Checks `$gate` (the agent version's own `spec.publish_gate`, already confirmed
     * `enabled` by the caller) against the most recent *completed* eval run for this
     * agent version - the same `agent_version_id` filter `EvalsController::
     * listEvalRuns` already supports, narrowed to the latest row. Returns a 400
     * `Response` describing which of the three ways the gate can fail, or `null` when
     * the gate is satisfied and publishing may proceed. `metric`/`threshold` are
     * guaranteed non-null by `PublishGateSpec`'s own validator (already run via
     * `compileAgentSpec()` before this is ever called), so this only has to resolve
     * the actual score.
     *
     * @param array<string, mixed> $gate
     */
    private function checkPublishGate(string $agentVersionId, array $gate): ?Response
    {
        $metric = $gate['metric'];
        $threshold = $gate['threshold'];

        $evalRun = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchOne(
                "SELECT summary FROM runtime.eval_runs
                 WHERE agent_version_id = :agent_version_id AND status = 'completed'
                 ORDER BY created_at DESC LIMIT 1",
                Enum::FETCH_ASSOC,
                ['agent_version_id' => $agentVersionId]
            )
        );
        if (empty($evalRun)) {
            return $this->jsonResponse(
                ['error' => 'publish_gate_failed', 'reason' => 'no_eval_run', 'metric' => $metric, 'threshold' => $threshold],
                400
            );
        }

        $summary = json_decode((string) $evalRun['summary'], true);
        $actual = $summary['mean_scores'][$metric] ?? null;
        if ($actual === null) {
            return $this->jsonResponse(
                ['error' => 'publish_gate_failed', 'reason' => 'metric_not_scored', 'metric' => $metric, 'threshold' => $threshold],
                400
            );
        }

        if ($actual < $threshold) {
            return $this->jsonResponse(
                [
                    'error' => 'publish_gate_failed',
                    'reason' => 'below_threshold',
                    'metric' => $metric,
                    'threshold' => $threshold,
                    'actual' => $actual,
                ],
                400
            );
        }

        return null;
    }

    /**
     * @param array<string, mixed> $row
     * @return array<string, mixed>
     */
    private function formatAgent(array $row): array
    {
        return [
            'id' => $row['id'],
            'company_id' => $row['company_id'],
            'name' => $row['name'],
            'description' => $row['description'],
            'status' => $row['status'],
            'archived_at' => $row['archived_at'],
            'created_at' => $row['created_at'],
            'updated_at' => $row['updated_at'],
        ];
    }

    /**
     * @param array<string, mixed> $row
     * @return array<string, mixed>
     */
    private function formatAgentVersion(array $row): array
    {
        // compiled_definition is the Advanced tab's admin-only raw JSON (PRD §6.1) - it
        // embeds resolved connection/tool/knowledge-base details (api_base, tool config/
        // audience, etc), not just the user-authored spec, so a non-admin company member
        // must never receive it even though they can read everything else about this
        // version. Redacted here, the one place every caller (listAgentVersions/
        // createAgentVersion/getAgentVersion) already funnels through, rather than at the
        // frontend alone - the web UI's isAdmin gating only controls whether it's
        // *rendered*, not whether the API hands it out.
        $isAdmin = $this->getRoleForCurrentCompany() === 'admin';
        return [
            'id' => $row['id'],
            'company_id' => $row['company_id'],
            'agent_id' => $row['agent_id'],
            'version_no' => (int) $row['version_no'],
            'spec' => json_decode((string) $row['spec'], true),
            'spec_version' => $row['spec_version'],
            'compiled_definition' => ($isAdmin && $row['compiled_definition'] !== null)
                ? json_decode((string) $row['compiled_definition'], true)
                : null,
            'compiler_version' => $row['compiler_version'],
            'dynamiq_version' => $row['dynamiq_version'],
            'is_published' => (bool) $row['is_published'],
            'published_by' => $row['published_by'],
            'published_at' => $row['published_at'],
            'created_at' => $row['created_at'],
            'updated_at' => $row['updated_at'],
        ];
    }
}
