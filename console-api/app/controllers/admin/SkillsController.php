<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Db\Enum;
use Phalcon\Http\Response;
use Throwable;

/**
 * /skills — company-scoped `SKILL.md` instructions, versioned + published (PRD §6.6a,
 * §8.1 `skills`/`skill_versions` tables).
 *
 * Content lives directly in `console.skill_versions.content` (Postgres `text`), not MinIO —
 * see the plan this was built from for why: nothing in this codebase has ever written to
 * MinIO, no client library is even a direct dependency, and a `SKILL.md` capped at 100 KB
 * (PRD §6.6a) fits a `text` column fine. Real MinIO-backed storage is the natural follow-up
 * once `.zip`/scripts/attachments support is built (phase 3, per the PRD's own phasing) — so,
 * unlike ConnectionsController/ToolsController, there is no RuntimeClient round trip here at
 * all: console-api owns this data end to end.
 *
 * Not yet wired into the agent spec/compiler, so no agent can actually load a skill via
 * SkillsTool during a run — same deferred-execution scope as Tools.
 *
 * Every mutating operation only needs `requireWriteRole()` (developer or admin) in this
 * slice — PRD §9.1's "skills with scripts: admin" gate has no code path to trigger yet,
 * since nothing here can set `has_scripts = true` (no script-upload path exists until the
 * phase-3 work above lands).
 */
class SkillsController extends ControllerBase
{
    private const MAX_CONTENT_BYTES = 102400; // 100 KB, PRD §6.6a

    public function listSkills(): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $rows = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchAll(
                "SELECT s.id, s.company_id, s.name, s.description, s.status, s.created_at, s.updated_at,
                        sv.id AS version_id, sv.version_no, sv.content, sv.content_hash, sv.has_scripts,
                        sv.is_published, sv.published_at
                 FROM console.skills s
                 JOIN LATERAL (
                     SELECT id, version_no, content, content_hash, has_scripts, is_published, published_at
                     FROM console.skill_versions
                     WHERE skill_id = s.id
                     ORDER BY version_no DESC LIMIT 1
                 ) sv ON true
                 ORDER BY s.created_at DESC",
                Enum::FETCH_ASSOC
            )
        );

        return $this->jsonResponse(array_map([$this, 'formatSkill'], $rows));
    }

    public function createSkill(): Response
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
        $content = $body['content'] ?? null;
        if (!is_string($content) || $content === '') {
            return $this->jsonResponse(['error' => 'content_required'], 400);
        }
        if (strlen($content) > self::MAX_CONTENT_BYTES) {
            return $this->jsonResponse(['error' => 'content_too_large', 'max_bytes' => self::MAX_CONTENT_BYTES], 400);
        }

        try {
            $row = $this->runInCompanyTransaction(function ($db) use ($name, $description, $content) {
                $companyId = $this->getCompanyId();
                $skill = $db->fetchOne(
                    'INSERT INTO console.skills (company_id, name, description)
                     VALUES (:company_id, :name, :description)
                     RETURNING id, company_id, name, description, status, created_at, updated_at',
                    Enum::FETCH_ASSOC,
                    ['company_id' => $companyId, 'name' => $name, 'description' => $description]
                );

                $version = $db->fetchOne(
                    'INSERT INTO console.skill_versions (company_id, skill_id, version_no, content, content_hash)
                     VALUES (:company_id, :skill_id, 1, :content, :content_hash)
                     RETURNING id, version_no, content, content_hash, has_scripts, is_published, published_at',
                    Enum::FETCH_ASSOC,
                    [
                        'company_id' => $companyId,
                        'skill_id' => $skill['id'],
                        'content' => $content,
                        'content_hash' => hash('sha256', $content),
                    ]
                );

                // Explicit field-by-field merge, not array_merge($skill, ..., $version) -
                // both RETURNING results have their own 'id' column (skill id vs version
                // id), and array_merge would let the version's 'id' silently clobber the
                // skill's in the combined row formatSkill() expects.
                return array_merge($skill, [
                    'version_id' => $version['id'],
                    'version_no' => $version['version_no'],
                    'content' => $version['content'],
                    'content_hash' => $version['content_hash'],
                    'has_scripts' => $version['has_scripts'],
                    'is_published' => $version['is_published'],
                    'published_at' => $version['published_at'],
                ]);
            });
        } catch (Throwable $exception) {
            if ($this->isUniqueViolation($exception)) {
                return $this->jsonResponse(['error' => 'name_already_exists'], 409);
            }
            throw $exception;
        }

        return $this->jsonResponse($this->formatSkill($row), 201);
    }

    public function updateSkill(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireWriteRole()) !== null) {
            return $error;
        }

        $existing = $this->findSkill($id);
        if ($existing === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        $body = $this->request->getJsonRawBody(true) ?? [];
        $skillFields = [];
        if (array_key_exists('name', $body)) {
            if (!is_string($body['name']) || trim($body['name']) === '') {
                return $this->jsonResponse(['error' => 'name_required'], 400);
            }
            $skillFields['name'] = $body['name'];
        }
        if (array_key_exists('description', $body)) {
            if ($body['description'] !== null && !is_string($body['description'])) {
                return $this->jsonResponse(['error' => 'description_must_be_string_or_null'], 400);
            }
            $skillFields['description'] = $body['description'];
        }
        if (array_key_exists('status', $body)) {
            if (!is_string($body['status']) || trim($body['status']) === '') {
                return $this->jsonResponse(['error' => 'status_required'], 400);
            }
            $skillFields['status'] = $body['status'];
        }

        $content = null;
        if (array_key_exists('content', $body)) {
            if (!is_string($body['content']) || $body['content'] === '') {
                return $this->jsonResponse(['error' => 'content_required'], 400);
            }
            if (strlen($body['content']) > self::MAX_CONTENT_BYTES) {
                return $this->jsonResponse(
                    ['error' => 'content_too_large', 'max_bytes' => self::MAX_CONTENT_BYTES],
                    400
                );
            }
            $content = $body['content'];
        }

        $row = $this->runInCompanyTransaction(function ($db) use ($id, $existing, $skillFields, $content) {
            if ($skillFields !== []) {
                $params = ['id' => $id];
                $setClauses = [];
                foreach ($skillFields as $column => $value) {
                    $setClauses[] = "{$column} = :{$column}";
                    $params[$column] = $value;
                }
                $setClauses[] = 'updated_at = now()';
                $db->execute('UPDATE console.skills SET ' . implode(', ', $setClauses) . ' WHERE id = :id', $params);
            }

            if ($content !== null) {
                $companyId = $this->getCompanyId();
                $contentHash = hash('sha256', $content);

                // A still-draft latest version is edited in place; a published one is
                // immutable (same rule as agent_versions), so a new version is created
                // instead — the only route to a new version at all, since (matching the
                // OpenAPI contract, which has no separate "create version" operation) that's
                // the sole way this slice exposes one.
                if (!(bool) $existing['is_published']) {
                    $db->execute(
                        'UPDATE console.skill_versions
                         SET content = :content, content_hash = :content_hash, updated_at = now()
                         WHERE id = :id',
                        ['content' => $content, 'content_hash' => $contentHash, 'id' => $existing['version_id']]
                    );
                } else {
                    $db->execute(
                        'INSERT INTO console.skill_versions
                            (company_id, skill_id, version_no, content, content_hash)
                         VALUES (:company_id, :skill_id, :version_no, :content, :content_hash)',
                        [
                            'company_id' => $companyId,
                            'skill_id' => $id,
                            'version_no' => (int) $existing['version_no'] + 1,
                            'content' => $content,
                            'content_hash' => $contentHash,
                        ]
                    );
                }
            }

            return $db->fetchOne(
                "SELECT s.id, s.company_id, s.name, s.description, s.status, s.created_at, s.updated_at,
                        sv.id AS version_id, sv.version_no, sv.content, sv.content_hash, sv.has_scripts,
                        sv.is_published, sv.published_at
                 FROM console.skills s
                 JOIN LATERAL (
                     SELECT id, version_no, content, content_hash, has_scripts, is_published, published_at
                     FROM console.skill_versions
                     WHERE skill_id = s.id
                     ORDER BY version_no DESC LIMIT 1
                 ) sv ON true
                 WHERE s.id = :id",
                Enum::FETCH_ASSOC,
                ['id' => $id]
            );
        });

        if (empty($row)) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        return $this->jsonResponse($this->formatSkill($row));
    }

    public function publishSkill(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireWriteRole()) !== null) {
            return $error;
        }

        $existing = $this->findSkill($id);
        if ($existing === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }
        if ((bool) $existing['is_published']) {
            return $this->jsonResponse(['error' => 'already_published'], 409);
        }

        $userId = $this->getAuthUser()['id'];

        $result = $this->runInCompanyTransaction(function ($db) use ($id, $existing, $userId) {
            // `AND is_published = false` closes the race between the check above and this
            // write — same pattern as AgentsController::publishAgentVersion.
            $db->execute(
                'UPDATE console.skill_versions
                 SET is_published = true, published_by = :published_by, published_at = now(), updated_at = now()
                 WHERE id = :id AND is_published = false',
                ['published_by' => $userId, 'id' => $existing['version_id']]
            );
            $updated = $db->affectedRows() > 0;

            $row = $db->fetchOne(
                "SELECT s.id, s.company_id, s.name, s.description, s.status, s.created_at, s.updated_at,
                        sv.id AS version_id, sv.version_no, sv.content, sv.content_hash, sv.has_scripts,
                        sv.is_published, sv.published_at
                 FROM console.skills s
                 JOIN LATERAL (
                     SELECT id, version_no, content, content_hash, has_scripts, is_published, published_at
                     FROM console.skill_versions
                     WHERE skill_id = s.id
                     ORDER BY version_no DESC LIMIT 1
                 ) sv ON true
                 WHERE s.id = :id",
                Enum::FETCH_ASSOC,
                ['id' => $id]
            );

            return ['updated' => $updated, 'row' => $row];
        });

        if (!$result['updated']) {
            return $this->jsonResponse(['error' => 'already_published'], 409);
        }

        return $this->jsonResponse($this->formatSkill($result['row']));
    }

    private function isUniqueViolation(Throwable $exception): bool
    {
        return $exception instanceof \PDOException && ($exception->errorInfo[0] ?? null) === '23505';
    }

    /**
     * @return array<string, mixed>|null
     */
    private function findSkill(string $id): ?array
    {
        if (!$this->isUuid($id)) {
            return null;
        }

        $row = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchOne(
                "SELECT s.id, s.company_id, s.name, s.description, s.status, s.created_at, s.updated_at,
                        sv.id AS version_id, sv.version_no, sv.content, sv.content_hash, sv.has_scripts,
                        sv.is_published, sv.published_at
                 FROM console.skills s
                 JOIN LATERAL (
                     SELECT id, version_no, content, content_hash, has_scripts, is_published, published_at
                     FROM console.skill_versions
                     WHERE skill_id = s.id
                     ORDER BY version_no DESC LIMIT 1
                 ) sv ON true
                 WHERE s.id = :id",
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
    private function formatSkill(array $row): array
    {
        return [
            'id' => $row['id'],
            'company_id' => $row['company_id'],
            'name' => $row['name'],
            'description' => $row['description'],
            'status' => $row['status'],
            'latest_version' => [
                'id' => $row['version_id'],
                'version_no' => (int) $row['version_no'],
                'content' => $row['content'],
                'content_hash' => $row['content_hash'],
                'has_scripts' => (bool) $row['has_scripts'],
                'is_published' => (bool) $row['is_published'],
                'published_at' => $row['published_at'],
            ],
            'created_at' => $row['created_at'],
            'updated_at' => $row['updated_at'],
        ];
    }
}
