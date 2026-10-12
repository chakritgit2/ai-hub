<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Db\Enum;
use Phalcon\Http\Response;
use Throwable;

/**
 * /datasets — company-scoped Evaluation datasets (PRD §6.7, §8.1/§8.2
 * `datasets`/`dataset_items` tables). Pure console.* CRUD plus a CSV/JSON item
 * import - unlike Knowledge Bases/Tools, this never talks to ai-runtime: there is
 * no Python-side counterpart for a dataset, it's just test-case storage that Eval
 * Runs (not yet implemented) will read from later.
 */
class DatasetsController extends ControllerBase
{
    private const MAX_IMPORT_FILE_BYTES = 10 * 1024 * 1024; // 10 MB, same default as KbController -
    // PRD doesn't specify a number for this either.
    private const SUPPORTED_IMPORT_EXTENSIONS = ['csv', 'json'];

    public function listDatasets(): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $rows = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchAll(
                'SELECT id, company_id, name, created_at, updated_at
                 FROM console.datasets ORDER BY created_at DESC',
                Enum::FETCH_ASSOC
            )
        );

        return $this->jsonResponse(array_map([$this, 'formatDataset'], $rows));
    }

    public function getDataset(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $existing = $this->findDataset($id);
        if ($existing === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        return $this->jsonResponse($this->formatDataset($existing));
    }

    public function createDataset(): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireWriteRole()) !== null) {
            return $error;
        }

        $body = $this->request->getJsonRawBody(true) ?? [];

        [$fields, $error] = $this->validateDatasetFields($body);
        if ($error !== null) {
            return $error;
        }
        if (!array_key_exists('name', $fields)) {
            return $this->jsonResponse(['error' => 'name_required'], 400);
        }

        try {
            $row = $this->runInCompanyTransaction(
                fn ($db) => $db->fetchOne(
                    'INSERT INTO console.datasets (company_id, name)
                     VALUES (:company_id, :name)
                     RETURNING id, company_id, name, created_at, updated_at',
                    Enum::FETCH_ASSOC,
                    ['company_id' => $this->getCompanyId(), 'name' => $fields['name']]
                )
            );
        } catch (Throwable $exception) {
            if ($this->isUniqueViolation($exception)) {
                return $this->jsonResponse(['error' => 'name_already_exists'], 409);
            }
            throw $exception;
        }

        return $this->jsonResponse($this->formatDataset($row), 201);
    }

    public function updateDataset(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireWriteRole()) !== null) {
            return $error;
        }

        $existing = $this->findDataset($id);
        if ($existing === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        $body = $this->request->getJsonRawBody(true) ?? [];

        [$fields, $error] = $this->validateDatasetFields($body);
        if ($error !== null) {
            return $error;
        }

        try {
            $row = $this->runInCompanyTransaction(function ($db) use ($id, $fields) {
                if ($fields !== []) {
                    $params = ['id' => $id, 'name' => $fields['name']];
                    $db->execute(
                        'UPDATE console.datasets SET name = :name, updated_at = now() WHERE id = :id',
                        $params
                    );
                }

                return $db->fetchOne(
                    'SELECT id, company_id, name, created_at, updated_at
                     FROM console.datasets WHERE id = :id',
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

        return $this->jsonResponse($this->formatDataset($row));
    }

    public function deleteDataset(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireWriteRole()) !== null) {
            return $error;
        }

        $existing = $this->findDataset($id);
        if ($existing === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        // ON DELETE CASCADE (console.dataset_items.dataset_id) takes care of the items -
        // no second step/cleanup needed, unlike KbController::deleteKnowledgeBase (no
        // ai-runtime side to this feature at all).
        $this->runInCompanyTransaction(
            static fn ($db) => $db->execute('DELETE FROM console.datasets WHERE id = :id', ['id' => $id])
        );

        return $this->noContentResponse();
    }

    public function listDatasetItems(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $existing = $this->findDataset($id);
        if ($existing === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        $rows = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchAll(
                'SELECT id, question, expected_answer, context, tags, created_at
                 FROM console.dataset_items WHERE dataset_id = :dataset_id ORDER BY created_at ASC',
                Enum::FETCH_ASSOC,
                ['dataset_id' => $id]
            )
        );

        return $this->jsonResponse(array_map([$this, 'formatDatasetItem'], $rows));
    }

    public function importDatasetItems(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireWriteRole()) !== null) {
            return $error;
        }

        $existing = $this->findDataset($id);
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

        $extension = strtolower(pathinfo($file->getName(), PATHINFO_EXTENSION));
        if (!in_array($extension, self::SUPPORTED_IMPORT_EXTENSIONS, true)) {
            return $this->jsonResponse(
                ['error' => 'unsupported_file_type', 'allowed' => self::SUPPORTED_IMPORT_EXTENSIONS],
                422
            );
        }

        $contents = (string) file_get_contents($file->getTempName());
        [$rawRows, $error] = $extension === 'csv'
            ? $this->parseCsvRows($contents)
            : $this->parseJsonRows($contents);
        if ($error !== null) {
            return $error;
        }

        if ($rawRows === []) {
            return $this->jsonResponse(['error' => 'file_contains_no_items'], 422);
        }

        $items = [];
        $errors = [];
        foreach ($rawRows as $entry) {
            [$item, $rowError] = $this->validateDatasetItemRow($entry['data']);
            if ($rowError !== null) {
                $errors[] = ['row' => $entry['line'], 'error' => $rowError];
                continue;
            }
            $items[] = $item;
        }

        $inserted = $this->runInCompanyTransaction(function ($db) use ($id, $items) {
            $companyId = $this->getCompanyId();
            foreach ($items as $item) {
                $db->execute(
                    'INSERT INTO console.dataset_items
                        (company_id, dataset_id, question, expected_answer, context, tags)
                     VALUES (:company_id, :dataset_id, :question, :expected_answer, :context, :tags::jsonb)',
                    [
                        'company_id' => $companyId,
                        'dataset_id' => $id,
                        'question' => $item['question'],
                        'expected_answer' => $item['expected_answer'],
                        'context' => $item['context'],
                        'tags' => json_encode($item['tags']),
                    ]
                );
            }

            return count($items);
        });

        return $this->jsonResponse(['inserted' => $inserted, 'errors' => $errors], 200);
    }

    /**
     * Each returned entry's `line` is the 1-based physical line number in the uploaded
     * file (header = line 1), so validation errors can point back at the exact line the
     * user needs to fix - not just a sequence number over the non-blank data rows.
     *
     * @return array{0: array<int, array{line: int, data: array<string, mixed>}>, 1: ?Response}
     */
    private function parseCsvRows(string $contents): array
    {
        $stream = fopen('php://memory', 'r+');
        fwrite($stream, $contents);
        rewind($stream);

        $header = fgetcsv($stream);
        if ($header === false) {
            fclose($stream);

            return [[], null];
        }
        $header = array_map(static fn ($column) => strtolower(trim((string) $column)), $header);
        $lineNumber = 1;

        $rows = [];
        while (($line = fgetcsv($stream)) !== false) {
            $lineNumber++;
            if ($line === [null] || $line === []) {
                continue; // blank line
            }
            $row = [];
            foreach ($header as $columnIndex => $columnName) {
                $row[$columnName] = $line[$columnIndex] ?? null;
            }
            $rows[] = ['line' => $lineNumber, 'data' => $row];
        }
        fclose($stream);

        return [$rows, null];
    }

    /**
     * @return array{0: array<int, array{line: int, data: array<string, mixed>}>, 1: ?Response}
     */
    private function parseJsonRows(string $contents): array
    {
        $decoded = json_decode($contents, true);
        if (!is_array($decoded)) {
            return [[], $this->jsonResponse(['error' => 'invalid_json_file'], 422)];
        }

        $rows = [];
        foreach (array_values($decoded) as $index => $row) {
            if (!is_array($row)) {
                return [[], $this->jsonResponse(['error' => 'invalid_json_file'], 422)];
            }
            $rows[] = ['line' => $index + 1, 'data' => $row];
        }

        return [$rows, null];
    }

    /**
     * @param array<string, mixed> $row
     * @return array{0: array{question: string, expected_answer: ?string, context: ?string, tags: array<int, string>}|null, 1: ?string}
     */
    private function validateDatasetItemRow(array $row): array
    {
        $question = isset($row['question']) ? trim((string) $row['question']) : '';
        if ($question === '') {
            return [null, 'question_required'];
        }

        $expectedAnswer = isset($row['expected_answer']) && trim((string) $row['expected_answer']) !== ''
            ? trim((string) $row['expected_answer'])
            : null;
        $context = isset($row['context']) && trim((string) $row['context']) !== ''
            ? trim((string) $row['context'])
            : null;

        $tags = [];
        if (isset($row['tags'])) {
            if (is_array($row['tags'])) {
                $tags = array_values(array_filter(array_map('strval', $row['tags']), static fn ($tag) => trim($tag) !== ''));
            } elseif (is_string($row['tags']) && trim($row['tags']) !== '') {
                $tags = array_values(array_filter(array_map('trim', explode(';', $row['tags'])), static fn ($tag) => $tag !== ''));
            }
        }

        return [
            ['question' => $question, 'expected_answer' => $expectedAnswer, 'context' => $context, 'tags' => $tags],
            null,
        ];
    }

    /**
     * @param array<string, mixed> $body
     * @return array{0: array<string, mixed>, 1: ?Response}
     */
    private function validateDatasetFields(array $body): array
    {
        $fields = [];

        if (array_key_exists('name', $body)) {
            if (!is_string($body['name']) || trim($body['name']) === '') {
                return [[], $this->jsonResponse(['error' => 'name_required'], 400)];
            }
            $fields['name'] = $body['name'];
        }

        return [$fields, null];
    }

    private function isUniqueViolation(Throwable $exception): bool
    {
        return $exception instanceof \PDOException && ($exception->errorInfo[0] ?? null) === '23505';
    }

    /**
     * @return array<string, mixed>|null
     */
    private function findDataset(string $id): ?array
    {
        if (!$this->isUuid($id)) {
            return null;
        }

        $row = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchOne(
                'SELECT id, company_id, name, created_at, updated_at
                 FROM console.datasets WHERE id = :id',
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
    private function formatDataset(array $row): array
    {
        return [
            'id' => $row['id'],
            'company_id' => $row['company_id'],
            'name' => $row['name'],
            'created_at' => $row['created_at'],
            'updated_at' => $row['updated_at'],
        ];
    }

    /**
     * @param array<string, mixed> $row
     * @return array<string, mixed>
     */
    private function formatDatasetItem(array $row): array
    {
        return [
            'id' => $row['id'],
            'question' => $row['question'],
            'expected_answer' => $row['expected_answer'],
            'context' => $row['context'],
            'tags' => json_decode((string) $row['tags'], true) ?? [],
            'created_at' => $row['created_at'],
        ];
    }
}
