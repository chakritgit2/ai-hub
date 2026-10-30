<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Db\Enum;
use Phalcon\Http\Response;
use Throwable;

/**
 * /settings/model-pricing — platform_admin only, no X-Company-Id (PRD §8.2
 * `model_pricing` table: a flat $/1K-token rate reference, not company-scoped, same
 * no-RLS shape as console.companies/console.tenants). Only consumer today is
 * ai/app/services/model_pricing.py's eval-cost estimate (PRD §6.7) - real run cost
 * accounting goes through litellm's own built-in pricing instead, untouched by this
 * table. Before this controller the table's own migration comment said rows were
 * "maintained directly via migration/SQL" - this is that admin UI.
 */
class PricingController extends ControllerBase
{
    private const LIST_PRICING_SQL = 'SELECT id, provider, model, input_per_1k, output_per_1k, created_at, updated_at
         FROM console.model_pricing ORDER BY provider, model';

    // Match console.model_pricing's own varchar(64)/varchar(128) columns (20261014000001)
    // - validate here so an overlong value gets a clean 400 instead of an uncaught
    // Postgres "value too long" exception.
    private const PROVIDER_MAX_LENGTH = 64;
    private const MODEL_MAX_LENGTH = 128;

    public function listModelPricing(): Response
    {
        if (($error = $this->requirePlatformAdmin()) !== null) {
            return $error;
        }

        $db = $this->getDI()->getShared('db');
        $rows = $db->fetchAll(self::LIST_PRICING_SQL, Enum::FETCH_ASSOC);

        return $this->jsonResponse(array_map([$this, 'formatPricing'], $rows));
    }

    public function createModelPricing(): Response
    {
        if (($error = $this->requirePlatformAdmin()) !== null) {
            return $error;
        }

        $body = $this->request->getJsonRawBody(true) ?? [];

        [$provider, $error] = $this->validateNameField($body['provider'] ?? null, 'provider', self::PROVIDER_MAX_LENGTH);
        if ($error !== null) {
            return $error;
        }
        [$model, $error] = $this->validateNameField($body['model'] ?? null, 'model', self::MODEL_MAX_LENGTH);
        if ($error !== null) {
            return $error;
        }
        [$inputPer1k, $error] = $this->validatePriceField($body['input_per_1k'] ?? null, 'input_per_1k');
        if ($error !== null) {
            return $error;
        }
        [$outputPer1k, $error] = $this->validatePriceField($body['output_per_1k'] ?? null, 'output_per_1k');
        if ($error !== null) {
            return $error;
        }

        $db = $this->getDI()->getShared('db');
        try {
            $row = $db->fetchOne(
                'INSERT INTO console.model_pricing (provider, model, input_per_1k, output_per_1k)
                 VALUES (:provider, :model, :input_per_1k, :output_per_1k)
                 RETURNING id, provider, model, input_per_1k, output_per_1k, created_at, updated_at',
                Enum::FETCH_ASSOC,
                [
                    'provider' => $provider,
                    'model' => $model,
                    'input_per_1k' => $inputPer1k,
                    'output_per_1k' => $outputPer1k,
                ]
            );
        } catch (Throwable $exception) {
            if ($this->isUniqueViolation($exception)) {
                return $this->jsonResponse(['error' => 'provider_model_already_exists'], 409);
            }
            throw $exception;
        }

        return $this->jsonResponse($this->formatPricing($row), 201);
    }

    public function updateModelPricing(string $id): Response
    {
        if (($error = $this->requirePlatformAdmin()) !== null) {
            return $error;
        }
        if (!$this->isUuid($id)) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        $body = $this->request->getJsonRawBody(true) ?? [];
        $fields = [];
        if (array_key_exists('provider', $body)) {
            [$provider, $error] = $this->validateNameField($body['provider'], 'provider', self::PROVIDER_MAX_LENGTH);
            if ($error !== null) {
                return $error;
            }
            $fields['provider'] = $provider;
        }
        if (array_key_exists('model', $body)) {
            [$model, $error] = $this->validateNameField($body['model'], 'model', self::MODEL_MAX_LENGTH);
            if ($error !== null) {
                return $error;
            }
            $fields['model'] = $model;
        }
        if (array_key_exists('input_per_1k', $body)) {
            [$inputPer1k, $error] = $this->validatePriceField($body['input_per_1k'], 'input_per_1k');
            if ($error !== null) {
                return $error;
            }
            $fields['input_per_1k'] = $inputPer1k;
        }
        if (array_key_exists('output_per_1k', $body)) {
            [$outputPer1k, $error] = $this->validatePriceField($body['output_per_1k'], 'output_per_1k');
            if ($error !== null) {
                return $error;
            }
            $fields['output_per_1k'] = $outputPer1k;
        }

        $db = $this->getDI()->getShared('db');
        try {
            if ($fields !== []) {
                $params = ['id' => $id];
                $setClauses = [];
                foreach ($fields as $column => $value) {
                    $setClauses[] = "{$column} = :{$column}";
                    $params[$column] = $value;
                }
                $setClauses[] = 'updated_at = now()';
                $db->execute(
                    'UPDATE console.model_pricing SET ' . implode(', ', $setClauses) . ' WHERE id = :id',
                    $params
                );
            }

            $row = $db->fetchOne(
                'SELECT id, provider, model, input_per_1k, output_per_1k, created_at, updated_at
                 FROM console.model_pricing WHERE id = :id',
                Enum::FETCH_ASSOC,
                ['id' => $id]
            );
        } catch (Throwable $exception) {
            if ($this->isUniqueViolation($exception)) {
                return $this->jsonResponse(['error' => 'provider_model_already_exists'], 409);
            }
            throw $exception;
        }

        if (empty($row)) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        return $this->jsonResponse($this->formatPricing($row));
    }

    public function deleteModelPricing(string $id): Response
    {
        if (($error = $this->requirePlatformAdmin()) !== null) {
            return $error;
        }
        if (!$this->isUuid($id)) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        $db = $this->getDI()->getShared('db');
        $db->execute('DELETE FROM console.model_pricing WHERE id = :id', ['id' => $id]);

        if ($db->affectedRows() === 0) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        return $this->noContentResponse();
    }

    private function isUniqueViolation(Throwable $exception): bool
    {
        return $exception instanceof \PDOException && ($exception->errorInfo[0] ?? null) === '23505';
    }

    /**
     * Trims and length-checks a `provider`/`model` string, catching what would otherwise
     * reach Postgres as either an empty value or a "value too long for type character
     * varying" exception the unique-violation catch block doesn't recognize.
     *
     * @return array{0: ?string, 1: ?Response}
     */
    private function validateNameField(mixed $value, string $field, int $maxLength): array
    {
        if (!is_string($value)) {
            return [null, $this->jsonResponse(['error' => "{$field}_required"], 400)];
        }
        $trimmed = trim($value);
        if ($trimmed === '') {
            return [null, $this->jsonResponse(['error' => "{$field}_required"], 400)];
        }
        if (strlen($trimmed) > $maxLength) {
            return [null, $this->jsonResponse(['error' => "{$field}_too_long"], 400)];
        }

        return [$trimmed, null];
    }

    /**
     * `console.model_pricing.input_per_1k`/`output_per_1k` both have a `CHECK (... >= 0)`
     * constraint (20261014000001) - validate non-negativity here too, since that's a
     * check_violation (SQLSTATE 23514), a code `isUniqueViolation()` doesn't recognize,
     * so an uncaught negative value would otherwise rethrow as a raw 500.
     *
     * @return array{0: ?float, 1: ?Response}
     */
    private function validatePriceField(mixed $value, string $field): array
    {
        if (!is_numeric($value) || (float) $value < 0) {
            return [null, $this->jsonResponse(['error' => "invalid_{$field}"], 400)];
        }

        return [(float) $value, null];
    }

    /**
     * @param array<string, mixed> $row
     * @return array<string, mixed>
     */
    private function formatPricing(array $row): array
    {
        return [
            'id' => $row['id'],
            'provider' => $row['provider'],
            'model' => $row['model'],
            'input_per_1k' => (float) $row['input_per_1k'],
            'output_per_1k' => (float) $row['output_per_1k'],
            'created_at' => $row['created_at'],
            'updated_at' => $row['updated_at'],
        ];
    }
}
