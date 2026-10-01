<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use ConsoleApi\Services\RuntimeClient;
use Phalcon\Db\Enum;
use Phalcon\Http\Response;
use Throwable;

/**
 * /agents and /agents/{id}/versions* — agent specs and their published versions
 * (PRD §4.4-A, §8.1 `agents` + `agent_versions` tables).
 *
 * `export`/`importAgent` stay 501 (x-phase: 3, see contracts/openapi/console-api.yaml).
 * Everything else is real.
 */
class AgentsController extends ControllerBase
{
    public function listAgents(): Response
    {
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

    public function exportAgent(string $id): Response
    {
        return $this->notImplemented();
    }

    public function importAgent(string $id): Response
    {
        return $this->notImplemented();
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
        if ($this->findAgent($id) === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        $body = $this->request->getJsonRawBody(true) ?? [];
        $spec = $body['spec'] ?? null;
        if (!is_array($spec)) {
            return $this->jsonResponse(['error' => 'spec_required'], 400);
        }
        $specVersion = is_string($body['spec_version'] ?? null) && $body['spec_version'] !== '' ? $body['spec_version'] : '1';

        try {
            $row = $this->runInCompanyTransaction(function ($db) use ($id, $spec, $specVersion) {
                $companyId = $this->getCompanyId();
                $next = $db->fetchOne(
                    'SELECT COALESCE(MAX(version_no), 0) + 1 AS next_version_no
                     FROM console.agent_versions WHERE agent_id = :agent_id',
                    Enum::FETCH_ASSOC,
                    ['agent_id' => $id]
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
                        'agent_id' => $id,
                        'version_no' => $next['next_version_no'],
                        'spec' => json_encode($spec),
                        'spec_version' => $specVersion,
                    ]
                );
            });
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
                // No Deployments CRUD exists yet (still a 501 stub), so this never matches
                // any row today — kept here because it's part of what publishing means
                // (PRD §4.4-A) and costs nothing to leave wired for when Deployments lands.
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
        return [
            'id' => $row['id'],
            'company_id' => $row['company_id'],
            'agent_id' => $row['agent_id'],
            'version_no' => (int) $row['version_no'],
            'spec' => json_decode((string) $row['spec'], true),
            'spec_version' => $row['spec_version'],
            'compiled_definition' => $row['compiled_definition'] !== null
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
