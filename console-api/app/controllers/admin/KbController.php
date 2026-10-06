<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use ConsoleApi\Services\RuntimeClient;
use Phalcon\Db\Enum;
use Phalcon\Http\Response;
use RuntimeException;
use Throwable;
use ZipArchive;

/**
 * /kb — company-scoped Knowledge Bases (PRD §6.6, §8.1 `knowledge_bases` table).
 *
 * CRUD here (console.knowledge_bases) plus three operations that forward to
 * ai-runtime's `/internal/v1/kb/*` (import/search/export) — the actual OKF parsing,
 * MinIO storage, chunking/embedding and pgvector search all live on the Python side
 * (app/services/kb_indexer.py, kb_search.py, kb_export.py), not here.
 */
class KbController extends ControllerBase
{
    private const RETRIEVAL_MODES = ['vector', 'hybrid'];
    private const MAX_IMPORT_FILE_BYTES = 10 * 1024 * 1024; // 10 MB - PRD doesn't specify
    // a number; OKF markdown files are expected to be far smaller than this, chosen as a
    // generous-but-not-unbounded default (see plan).

    public function listKnowledgeBases(): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $rows = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchAll(
                'SELECT id, company_id, name, embedder_connection_id, chunk_size, chunk_overlap,
                        retrieval_mode, alpha, okf_field_map, created_at, updated_at
                 FROM console.knowledge_bases ORDER BY created_at DESC',
                Enum::FETCH_ASSOC
            )
        );

        return $this->jsonResponse(array_map([$this, 'formatKnowledgeBase'], $rows));
    }

    public function getKnowledgeBase(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $existing = $this->findKnowledgeBase($id);
        if ($existing === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        return $this->jsonResponse($this->formatKnowledgeBase($existing));
    }

    public function listKnowledgeBaseDocuments(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $existing = $this->findKnowledgeBase($id);
        if ($existing === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        /** @var RuntimeClient $runtimeClient */
        $runtimeClient = $this->getDI()->getShared('runtimeClient');
        try {
            $documents = $runtimeClient->listKnowledgeBaseDocuments($id);
        } catch (RuntimeException $exception) {
            return $this->jsonResponse(
                ['error' => 'documents_request_failed', 'message' => $exception->getMessage()],
                502
            );
        }

        return $this->jsonResponse($documents);
    }

    public function createKnowledgeBase(): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireWriteRole()) !== null) {
            return $error;
        }

        $body = $this->request->getJsonRawBody(true) ?? [];

        [$fields, $error] = $this->validateKnowledgeBaseFields($body);
        if ($error !== null) {
            return $error;
        }
        $fields += ['name' => null, 'embedder_connection_id' => null, 'chunk_size' => 800,
            'chunk_overlap' => 100, 'retrieval_mode' => 'vector', 'alpha' => 0.60, 'okf_field_map' => []];

        if ($fields['name'] === null) {
            return $this->jsonResponse(['error' => 'name_required'], 400);
        }
        if ($fields['embedder_connection_id'] === null) {
            return $this->jsonResponse(['error' => 'embedder_connection_id_required'], 400);
        }

        if (($error = $this->assertConnectionBelongsToCompany($fields['embedder_connection_id'])) !== null) {
            return $error;
        }

        try {
            $row = $this->runInCompanyTransaction(
                fn ($db) => $db->fetchOne(
                    'INSERT INTO console.knowledge_bases
                        (company_id, name, embedder_connection_id, chunk_size, chunk_overlap,
                         retrieval_mode, alpha, okf_field_map)
                     VALUES (:company_id, :name, :embedder_connection_id, :chunk_size, :chunk_overlap,
                             :retrieval_mode, :alpha, :okf_field_map::jsonb)
                     RETURNING id, company_id, name, embedder_connection_id, chunk_size, chunk_overlap,
                               retrieval_mode, alpha, okf_field_map, created_at, updated_at',
                    Enum::FETCH_ASSOC,
                    [
                        'company_id' => $this->getCompanyId(),
                        'name' => $fields['name'],
                        'embedder_connection_id' => $fields['embedder_connection_id'],
                        'chunk_size' => $fields['chunk_size'],
                        'chunk_overlap' => $fields['chunk_overlap'],
                        'retrieval_mode' => $fields['retrieval_mode'],
                        'alpha' => $fields['alpha'],
                        'okf_field_map' => $this->encodeJsonObject($fields['okf_field_map']),
                    ]
                )
            );
        } catch (Throwable $exception) {
            if ($this->isUniqueViolation($exception)) {
                return $this->jsonResponse(['error' => 'name_already_exists'], 409);
            }
            throw $exception;
        }

        return $this->jsonResponse($this->formatKnowledgeBase($row), 201);
    }

    public function updateKnowledgeBase(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireWriteRole()) !== null) {
            return $error;
        }

        $existing = $this->findKnowledgeBase($id);
        if ($existing === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        $body = $this->request->getJsonRawBody(true) ?? [];

        [$fields, $error] = $this->validateKnowledgeBaseFields($body);
        if ($error !== null) {
            return $error;
        }

        if (array_key_exists('embedder_connection_id', $fields)) {
            if (($error = $this->assertConnectionBelongsToCompany($fields['embedder_connection_id'])) !== null) {
                return $error;
            }
        }

        try {
            $row = $this->runInCompanyTransaction(function ($db) use ($id, $fields) {
                if ($fields !== []) {
                    $params = ['id' => $id];
                    $setClauses = [];
                    foreach ($fields as $column => $value) {
                        if ($column === 'okf_field_map') {
                            $setClauses[] = 'okf_field_map = :okf_field_map::jsonb';
                            $params['okf_field_map'] = $this->encodeJsonObject($value);
                            continue;
                        }
                        $setClauses[] = "{$column} = :{$column}";
                        $params[$column] = $value;
                    }
                    $setClauses[] = 'updated_at = now()';
                    $db->execute(
                        'UPDATE console.knowledge_bases SET ' . implode(', ', $setClauses) . ' WHERE id = :id',
                        $params
                    );
                }

                return $db->fetchOne(
                    'SELECT id, company_id, name, embedder_connection_id, chunk_size, chunk_overlap,
                            retrieval_mode, alpha, okf_field_map, created_at, updated_at
                     FROM console.knowledge_bases WHERE id = :id',
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

        return $this->jsonResponse($this->formatKnowledgeBase($row));
    }

    public function deleteKnowledgeBase(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireWriteRole()) !== null) {
            return $error;
        }

        $existing = $this->findKnowledgeBase($id);
        if ($existing === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        // No agent spec can reference a KB yet (AgentSpecDoc has no `knowledge` field -
        // phase 2 follow-up per the compiler's own docstring), so there are never any
        // real dependents to block on today - nothing to check here until that lands.

        // Delete the console row first, not ai-runtime's data first: if this fails
        // (DB blip/timeout), nothing else has been touched and the KB is safely
        // retryable. Doing it the other way around risks a "zombie" KB that still
        // lists but can no longer serve any operation if the second step then fails.
        $this->runInCompanyTransaction(
            static fn ($db) => $db->execute('DELETE FROM console.knowledge_bases WHERE id = :id', ['id' => $id])
        );

        /** @var RuntimeClient $runtimeClient */
        $runtimeClient = $this->getDI()->getShared('runtimeClient');
        try {
            $runtimeClient->deleteKnowledgeBaseData($id);
        } catch (RuntimeException $exception) {
            // Best-effort from here on: the KB is already gone from the catalog (the
            // authoritative delete above succeeded), so the user-visible operation is
            // done - a leftover MinIO prefix/vector table/kb_documents rows is an ops
            // cleanup concern, not a reason to report failure for an already-completed
            // delete. Same "log, don't fail the request" convention as AuditLogger::log().
            error_log("KbController::deleteKnowledgeBase: cleanup failed for {$id}: " . $exception->getMessage());
        }

        return $this->noContentResponse();
    }

    public function importKnowledgeBaseFiles(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireWriteRole()) !== null) {
            return $error;
        }

        $existing = $this->findKnowledgeBase($id);
        if ($existing === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        if (!$this->request->hasFiles()) {
            return $this->jsonResponse(['error' => 'file_required'], 400);
        }

        $files = $this->request->getUploadedFiles();
        $file = $files[0];

        if ($file->getSize() > self::MAX_IMPORT_FILE_BYTES) {
            return $this->jsonResponse(['error' => 'file_too_large', 'max_bytes' => self::MAX_IMPORT_FILE_BYTES], 413);
        }

        $filename = $file->getName();
        $extension = strtolower(pathinfo($filename, PATHINFO_EXTENSION));

        if ($extension === 'md') {
            $documents = [[
                'category' => null,
                'filename' => $filename,
                'content' => (string) file_get_contents($file->getTempName()),
            ]];
        } elseif ($extension === 'zip') {
            [$documents, $error] = $this->extractMarkdownFilesFromZip($file->getTempName());
            if ($error !== null) {
                return $error;
            }
        } else {
            return $this->jsonResponse(['error' => 'unsupported_file_type', 'allowed' => ['md', 'zip']], 422);
        }

        /** @var RuntimeClient $runtimeClient */
        $runtimeClient = $this->getDI()->getShared('runtimeClient');
        try {
            $result = $runtimeClient->enqueueKbDocumentIndexing($id, $documents);
        } catch (RuntimeException $exception) {
            return $this->jsonResponse(
                ['error' => 'import_request_failed', 'message' => $exception->getMessage()],
                502
            );
        }

        return $this->jsonResponse($result, 202);
    }

    public function exportKnowledgeBase(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $existing = $this->findKnowledgeBase($id);
        if ($existing === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        /** @var RuntimeClient $runtimeClient */
        $runtimeClient = $this->getDI()->getShared('runtimeClient');
        try {
            $zipBytes = $runtimeClient->exportKnowledgeBase($id);
        } catch (RuntimeException $exception) {
            return $this->jsonResponse(
                ['error' => 'export_request_failed', 'message' => $exception->getMessage()],
                502
            );
        }

        $filename = $this->escapeContentDispositionFilename($existing['name'] . '.zip');

        $response = new Response();
        $response->setStatusCode(200);
        $response->setContentType('application/zip');
        $response->setHeader('Content-Disposition', 'attachment; filename="' . $filename . '"');
        $response->setContent($zipBytes);

        return $response;
    }

    public function searchKnowledgeBase(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $existing = $this->findKnowledgeBase($id);
        if ($existing === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        $body = $this->request->getJsonRawBody(true) ?? [];
        if (!isset($body['query']) || !is_string($body['query']) || trim($body['query']) === '') {
            return $this->jsonResponse(['error' => 'query_required'], 400);
        }
        $query = ['query' => $body['query']];
        if (isset($body['top_k'])) {
            if (!is_int($body['top_k']) || $body['top_k'] < 1) {
                return $this->jsonResponse(['error' => 'top_k_must_be_positive_integer'], 400);
            }
            $query['top_k'] = $body['top_k'];
        }

        /** @var RuntimeClient $runtimeClient */
        $runtimeClient = $this->getDI()->getShared('runtimeClient');
        try {
            $result = $runtimeClient->searchKnowledgeBase($id, $query);
        } catch (RuntimeException $exception) {
            return $this->jsonResponse(
                ['error' => 'search_request_failed', 'message' => $exception->getMessage()],
                502
            );
        }

        return $this->jsonResponse($result);
    }

    /**
     * Unzips an uploaded `.zip` into console-api's own temp dir (PHP's `ZipArchive`,
     * same "zip" extension already confirmed enabled) and reads every `.md` entry -
     * mirrors `ai/app/integrations/zip_import.py::extract_markdown_files`'s behavior
     * (folder path -> category, non-.md entries skipped silently) so both the single-file
     * and zip-upload paths end up producing the identical `{category, filename, content}`
     * shape ai-runtime's import endpoint expects.
     *
     * @return array{0: array<int, array{category: ?string, filename: string, content: string}>, 1: ?Response}
     */
    private function extractMarkdownFilesFromZip(string $tempPath): array
    {
        $zip = new ZipArchive();
        if ($zip->open($tempPath) !== true) {
            return [[], $this->jsonResponse(['error' => 'invalid_zip_file'], 422)];
        }

        $documents = [];
        for ($i = 0; $i < $zip->numFiles; $i++) {
            $entryName = $zip->getNameIndex($i);
            if ($entryName === false || str_ends_with($entryName, '/') || !str_ends_with(strtolower($entryName), '.md')) {
                continue;
            }

            $content = $zip->getFromIndex($i);
            if ($content === false) {
                continue;
            }

            $lastSlash = strrpos($entryName, '/');
            $category = $lastSlash === false ? null : substr($entryName, 0, $lastSlash);
            $filename = $lastSlash === false ? $entryName : substr($entryName, $lastSlash + 1);

            $documents[] = ['category' => $category, 'filename' => $filename, 'content' => $content];
        }
        $zip->close();

        if ($documents === []) {
            return [[], $this->jsonResponse(['error' => 'zip_contains_no_markdown_files'], 422)];
        }

        return [$documents, null];
    }

    /**
     * Validates whatever fields are present in $body (all required on create via the
     * caller's own defaulting, all optional/partial on update). Returns [fields, null] on
     * success or [[], Response] on the first validation failure.
     *
     * @param array<string, mixed> $body
     * @return array{0: array<string, mixed>, 1: ?Response}
     */
    private function validateKnowledgeBaseFields(array $body): array
    {
        $fields = [];

        if (array_key_exists('name', $body)) {
            if (!is_string($body['name']) || trim($body['name']) === '') {
                return [[], $this->jsonResponse(['error' => 'name_required'], 400)];
            }
            $fields['name'] = $body['name'];
        }

        if (array_key_exists('embedder_connection_id', $body)) {
            if (!is_string($body['embedder_connection_id']) || !$this->isUuid($body['embedder_connection_id'])) {
                return [[], $this->jsonResponse(['error' => 'embedder_connection_id_invalid'], 400)];
            }
            $fields['embedder_connection_id'] = $body['embedder_connection_id'];
        }

        if (array_key_exists('chunk_size', $body)) {
            if (!is_int($body['chunk_size']) || $body['chunk_size'] <= 0) {
                return [[], $this->jsonResponse(['error' => 'chunk_size_must_be_positive_integer'], 400)];
            }
            $fields['chunk_size'] = $body['chunk_size'];
        }

        if (array_key_exists('chunk_overlap', $body)) {
            if (!is_int($body['chunk_overlap']) || $body['chunk_overlap'] < 0) {
                return [[], $this->jsonResponse(['error' => 'chunk_overlap_must_be_non_negative_integer'], 400)];
            }
            $fields['chunk_overlap'] = $body['chunk_overlap'];
        }

        if (array_key_exists('retrieval_mode', $body)) {
            if (!is_string($body['retrieval_mode']) || !in_array($body['retrieval_mode'], self::RETRIEVAL_MODES, true)) {
                return [[], $this->jsonResponse(
                    ['error' => 'retrieval_mode_invalid', 'allowed' => self::RETRIEVAL_MODES],
                    400
                )];
            }
            $fields['retrieval_mode'] = $body['retrieval_mode'];
        }

        if (array_key_exists('alpha', $body)) {
            if (!is_numeric($body['alpha']) || $body['alpha'] < 0 || $body['alpha'] > 1) {
                return [[], $this->jsonResponse(['error' => 'alpha_must_be_between_0_and_1'], 400)];
            }
            $fields['alpha'] = (float) $body['alpha'];
        }

        if (array_key_exists('okf_field_map', $body)) {
            if (!is_array($body['okf_field_map'])) {
                return [[], $this->jsonResponse(['error' => 'okf_field_map_must_be_object'], 400)];
            }
            $fields['okf_field_map'] = $body['okf_field_map'];
        }

        return [$fields, null];
    }

    /**
     * Confirms `embedder_connection_id` resolves to a connection owned by the current
     * company (RLS already scopes this query, so "belongs to another company" and
     * "doesn't exist" are indistinguishable - both read as not-found, PRD §12/§7.7).
     */
    private function assertConnectionBelongsToCompany(string $connectionId): ?Response
    {
        $row = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchOne(
                'SELECT id FROM console.connections WHERE id = :id',
                Enum::FETCH_ASSOC,
                ['id' => $connectionId]
            )
        );

        return empty($row) ? $this->jsonResponse(['error' => 'embedder_connection_not_found'], 422) : null;
    }

    /**
     * Same empty-object encoding fix as ToolsController::encodeToolConfig() - PHP's
     * json_encode([]) always yields '[]', never the '{}' a jsonb column needs.
     *
     * @param array<string, mixed> $value
     */
    private function encodeJsonObject(array $value): string
    {
        return $value === [] ? '{}' : json_encode($value);
    }

    private function isUniqueViolation(Throwable $exception): bool
    {
        return $exception instanceof \PDOException && ($exception->errorInfo[0] ?? null) === '23505';
    }

    /**
     * `name` is only validated as non-empty (validateKnowledgeBaseFields) - a `"` in it
     * would otherwise break out of the quoted filename segment of the Content-Disposition
     * header. Escapes backslashes and double quotes per RFC 6266's quoted-string syntax,
     * and strips CR/LF defensively (PHP's own header() already rejects embedded newlines,
     * but this keeps the value well-formed regardless of how the response is sent).
     */
    private function escapeContentDispositionFilename(string $filename): string
    {
        $filename = str_replace(["\r", "\n"], '', $filename);

        return str_replace(['\\', '"'], ['\\\\', '\\"'], $filename);
    }

    /**
     * @return array<string, mixed>|null
     */
    private function findKnowledgeBase(string $id): ?array
    {
        if (!$this->isUuid($id)) {
            return null;
        }

        $row = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchOne(
                'SELECT id, company_id, name, embedder_connection_id, chunk_size, chunk_overlap,
                        retrieval_mode, alpha, okf_field_map, created_at, updated_at
                 FROM console.knowledge_bases WHERE id = :id',
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
    private function formatKnowledgeBase(array $row): array
    {
        return [
            'id' => $row['id'],
            'company_id' => $row['company_id'],
            'name' => $row['name'],
            'embedder_connection_id' => $row['embedder_connection_id'],
            'chunk_size' => (int) $row['chunk_size'],
            'chunk_overlap' => (int) $row['chunk_overlap'],
            'retrieval_mode' => $row['retrieval_mode'],
            'alpha' => (float) $row['alpha'],
            'okf_field_map' => json_decode((string) $row['okf_field_map'], true),
            'created_at' => $row['created_at'],
            'updated_at' => $row['updated_at'],
        ];
    }
}
