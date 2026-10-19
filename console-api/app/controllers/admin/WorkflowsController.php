<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use ConsoleApi\Services\RuntimeClient;
use Phalcon\Db\Enum;
use Phalcon\Http\Response;
use Throwable;

/**
 * /workflows and /workflows/{id}/versions* — workflow specs and their published versions
 * (PRD §6.9, phase 4; §8.2 `workflows` + `workflow_versions` tables).
 *
 * Slice 1 scope: CRUD + compile/validate only — no execution, no deployment/gateway
 * exposure yet (both are explicit later slices). No clone/export/import (not asked for
 * this slice, unlike AgentsController). No publish-gate check and no dependent-
 * deployments config_version bump — neither concept exists for workflows yet.
 */
class WorkflowsController extends ControllerBase
{
    public function listWorkflows(): Response
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
                 FROM console.workflows ORDER BY created_at DESC',
                Enum::FETCH_ASSOC
            )
        );

        return $this->jsonResponse(array_map([$this, 'formatWorkflow'], $rows));
    }

    public function createWorkflow(): Response
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
                    'INSERT INTO console.workflows (company_id, name, description)
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

        return $this->jsonResponse($this->formatWorkflow($row), 201);
    }

    public function getWorkflow(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $row = $this->findWorkflow($id);
        if ($row === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        return $this->jsonResponse($this->formatWorkflow($row));
    }

    public function updateWorkflow(string $id): Response
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
                        'UPDATE console.workflows SET ' . implode(', ', $setClauses) . ' WHERE id = :id',
                        [...$fields, 'id' => $id]
                    );
                }

                return $db->fetchOne(
                    'SELECT id, company_id, name, description, status, archived_at, created_at, updated_at
                     FROM console.workflows WHERE id = :id',
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

        return $this->jsonResponse($this->formatWorkflow($row));
    }

    /**
     * Soft-deletes a workflow (PRD: no hard delete — workflow_versions references it, and
     * `archived_at` already exists on this table for exactly this). Same race-safety
     * pattern as AgentsController::archiveAgent.
     */
    public function archiveWorkflow(string $id): Response
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
                'SELECT id FROM console.workflows WHERE id = :id',
                Enum::FETCH_ASSOC,
                ['id' => $id]
            );
            if (empty($existing)) {
                return ['outcome' => 'not_found'];
            }

            $db->execute(
                "UPDATE console.workflows SET status = 'archived', archived_at = now(), updated_at = now()
                 WHERE id = :id AND archived_at IS NULL",
                ['id' => $id]
            );
            if ($db->affectedRows() === 0) {
                return ['outcome' => 'already_archived'];
            }

            return ['outcome' => 'ok', 'row' => $db->fetchOne(
                'SELECT id, company_id, name, description, status, archived_at, created_at, updated_at
                 FROM console.workflows WHERE id = :id',
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

        return $this->jsonResponse($this->formatWorkflow($result['row']));
    }

    /** Reverses archiveWorkflow - same single-transaction, WHERE-guarded, affectedRows() pattern. */
    public function unarchiveWorkflow(string $id): Response
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
                'SELECT id FROM console.workflows WHERE id = :id',
                Enum::FETCH_ASSOC,
                ['id' => $id]
            );
            if (empty($existing)) {
                return ['outcome' => 'not_found'];
            }

            $db->execute(
                "UPDATE console.workflows SET status = 'draft', archived_at = null, updated_at = now()
                 WHERE id = :id AND archived_at IS NOT NULL",
                ['id' => $id]
            );
            if ($db->affectedRows() === 0) {
                return ['outcome' => 'not_archived'];
            }

            return ['outcome' => 'ok', 'row' => $db->fetchOne(
                'SELECT id, company_id, name, description, status, archived_at, created_at, updated_at
                 FROM console.workflows WHERE id = :id',
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

        return $this->jsonResponse($this->formatWorkflow($result['row']));
    }

    public function listWorkflowVersions(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }
        if ($this->findWorkflow($id) === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        $rows = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchAll(
                'SELECT id, company_id, workflow_id, version_no, spec, spec_version, compiled_definition,
                        compiler_version, dynamiq_version, is_published, published_by, published_at,
                        created_at, updated_at
                 FROM console.workflow_versions WHERE workflow_id = :workflow_id ORDER BY version_no DESC',
                Enum::FETCH_ASSOC,
                ['workflow_id' => $id]
            )
        );

        return $this->jsonResponse(array_map([$this, 'formatWorkflowVersion'], $rows));
    }

    public function createWorkflowVersion(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireWriteRole()) !== null) {
            return $error;
        }
        $workflow = $this->findWorkflow($id);
        if ($workflow === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }
        if ($workflow['archived_at'] !== null) {
            return $this->jsonResponse(['error' => 'workflow_archived'], 409);
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
                     FROM console.workflow_versions WHERE workflow_id = :workflow_id',
                    Enum::FETCH_ASSOC,
                    ['workflow_id' => $id]
                );

                return $db->fetchOne(
                    'INSERT INTO console.workflow_versions (company_id, workflow_id, version_no, spec, spec_version)
                     VALUES (:company_id, :workflow_id, :version_no, :spec::jsonb, :spec_version)
                     RETURNING id, company_id, workflow_id, version_no, spec, spec_version, compiled_definition,
                               compiler_version, dynamiq_version, is_published, published_by, published_at,
                               created_at, updated_at',
                    Enum::FETCH_ASSOC,
                    [
                        'company_id' => $companyId,
                        'workflow_id' => $id,
                        'version_no' => $next['next_version_no'],
                        'spec' => json_encode($spec),
                        'spec_version' => $specVersion,
                    ]
                );
            });
        } catch (Throwable $exception) {
            // console.workflow_versions has UNIQUE (workflow_id, version_no) — two
            // concurrent creates for the same workflow can compute the same
            // next_version_no and race.
            if ($this->isUniqueViolation($exception)) {
                return $this->jsonResponse(['error' => 'version_conflict_retry'], 409);
            }
            throw $exception;
        }

        return $this->jsonResponse($this->formatWorkflowVersion($row), 201);
    }

    public function getWorkflowVersion(string $id, string $vid): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $row = $this->findWorkflowVersion($id, $vid);
        if ($row === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        return $this->jsonResponse($this->formatWorkflowVersion($row));
    }

    public function updateWorkflowVersion(string $id, string $vid): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireWriteRole()) !== null) {
            return $error;
        }

        $workflow = $this->findWorkflow($id);
        if ($workflow === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }
        if ($workflow['archived_at'] !== null) {
            return $this->jsonResponse(['error' => 'workflow_archived'], 409);
        }

        $existing = $this->findWorkflowVersion($id, $vid);
        if ($existing === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }
        if ((bool) $existing['is_published']) {
            // Publish makes a version immutable (PRD §6.9) — edit a new draft version instead.
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
                // publishWorkflowVersion's UPDATE — a concurrent publish between the
                // check above and this write shouldn't silently overwrite the now-
                // immutable row.
                $params = ['id' => $vid];
                foreach ($fields as $column => $value) {
                    $placeholder = $column === 'spec' ? ":{$column}::jsonb" : ":{$column}";
                    $setClauses[] = "{$column} = {$placeholder}";
                    $params[$column] = $value;
                }
                $setClauses[] = 'updated_at = now()';
                $db->execute(
                    'UPDATE console.workflow_versions SET ' . implode(', ', $setClauses)
                    . ' WHERE id = :id AND is_published = false',
                    $params
                );
            }

            return $db->fetchOne(
                'SELECT id, company_id, workflow_id, version_no, spec, spec_version, compiled_definition,
                        compiler_version, dynamiq_version, is_published, published_by, published_at,
                        created_at, updated_at
                 FROM console.workflow_versions WHERE id = :id',
                Enum::FETCH_ASSOC,
                ['id' => $vid]
            );
        });

        if ((bool) $row['is_published']) {
            // Lost the race against a concurrent publish between our check and our write.
            return $this->jsonResponse(['error' => 'version_is_published'], 409);
        }

        return $this->jsonResponse($this->formatWorkflowVersion($row));
    }

    /**
     * Recompiles via ai-runtime (RuntimeClient::compileWorkflowSpec), sets
     * is_published=true (PRD §6.9, phase 4). A compile failure (bad spec, cross-company
     * or unpublished agent reference, disallowed node type) leaves the version untouched
     * and is surfaced as 422 with the compiler's errors — publishing is all-or-nothing,
     * same as AgentsController::publishAgentVersion, including the dependent-deployments
     * config_version bump (PRD §6.3 deployment/gateway exposure). No publish-gate check
     * (that's an agent-specific PRD §6.7 concept).
     */
    public function publishWorkflowVersion(string $id, string $vid): Response
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

        $workflow = $this->findWorkflow($id);
        if ($workflow === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }
        if ($workflow['archived_at'] !== null) {
            return $this->jsonResponse(['error' => 'workflow_archived'], 409);
        }

        $existing = $this->findWorkflowVersion($id, $vid);
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
            $compileResult = $runtimeClient->compileWorkflowSpec($spec, $role);
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
                'UPDATE console.workflow_versions
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
                // Bumps config_version on every deployment bound to this workflow version,
                // same mechanism AgentsController::publishAgentVersion already uses (PRD
                // §4.4-A) - the gateway notices its cached compiled_definition is stale.
                $db->execute(
                    'UPDATE console.deployments SET config_version = config_version + 1
                     WHERE workflow_version_id = :workflow_version_id',
                    ['workflow_version_id' => $vid]
                );
            }

            $row = $db->fetchOne(
                'SELECT id, company_id, workflow_id, version_no, spec, spec_version, compiled_definition,
                        compiler_version, dynamiq_version, is_published, published_by, published_at,
                        created_at, updated_at
                 FROM console.workflow_versions WHERE id = :id',
                Enum::FETCH_ASSOC,
                ['id' => $vid]
            );

            return ['updated' => $updated, 'row' => $row];
        });

        if (!$result['updated']) {
            // Lost the race against a concurrent publish of this same version.
            return $this->jsonResponse(['error' => 'already_published'], 409);
        }

        return $this->jsonResponse($this->formatWorkflowVersion($result['row']));
    }

    private function isUniqueViolation(Throwable $exception): bool
    {
        return $exception instanceof \PDOException && ($exception->errorInfo[0] ?? null) === '23505';
    }

    /**
     * @return array<string, mixed>|null
     */
    private function findWorkflow(string $id): ?array
    {
        if (!$this->isUuid($id)) {
            return null;
        }

        $row = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchOne(
                'SELECT id, company_id, name, description, status, archived_at, created_at, updated_at
                 FROM console.workflows WHERE id = :id',
                Enum::FETCH_ASSOC,
                ['id' => $id]
            )
        );

        return empty($row) ? null : $row;
    }

    /**
     * @return array<string, mixed>|null
     */
    private function findWorkflowVersion(string $id, string $vid): ?array
    {
        if (!$this->isUuid($id) || !$this->isUuid($vid)) {
            return null;
        }

        $row = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchOne(
                'SELECT id, company_id, workflow_id, version_no, spec, spec_version, compiled_definition,
                        compiler_version, dynamiq_version, is_published, published_by, published_at,
                        created_at, updated_at
                 FROM console.workflow_versions WHERE id = :vid AND workflow_id = :id',
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
    private function formatWorkflow(array $row): array
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
    private function formatWorkflowVersion(array $row): array
    {
        // compiled_definition is admin-only raw JSON, same reasoning as
        // AgentsController::formatAgentVersion - it embeds resolved manager-connection
        // details and each referenced agent's own compiled_definition, not just the
        // user-authored spec, so a non-admin company member must never receive it even
        // though they can read everything else about this version.
        $isAdmin = $this->getRoleForCurrentCompany() === 'admin';
        return [
            'id' => $row['id'],
            'company_id' => $row['company_id'],
            'workflow_id' => $row['workflow_id'],
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
