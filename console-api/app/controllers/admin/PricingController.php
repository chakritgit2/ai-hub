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
        $provider = $body['provider'] ?? null;
        if (!is_string($provider) || trim($provider) === '') {
            return $this->jsonResponse(['error' => 'provider_required'], 400);
        }
        $model = $body['model'] ?? null;
        if (!is_string($model) || trim($model) === '') {
            return $this->jsonResponse(['error' => 'model_required'], 400);
        }
        if (!is_numeric($body['input_per_1k'] ?? null)) {
            return $this->jsonResponse(['error' => 'invalid_input_per_1k'], 400);
        }
        if (!is_numeric($body['output_per_1k'] ?? null)) {
            return $this->jsonResponse(['error' => 'invalid_output_per_1k'], 400);
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
                    'input_per_1k' => $body['input_per_1k'],
                    'output_per_1k' => $body['output_per_1k'],
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
            if (!is_string($body['provider']) || trim($body['provider']) === '') {
                return $this->jsonResponse(['error' => 'provider_required'], 400);
            }
            $fields['provider'] = $body['provider'];
        }
        if (array_key_exists('model', $body)) {
            if (!is_string($body['model']) || trim($body['model']) === '') {
                return $this->jsonResponse(['error' => 'model_required'], 400);
            }
            $fields['model'] = $body['model'];
        }
        if (array_key_exists('input_per_1k', $body)) {
            if (!is_numeric($body['input_per_1k'])) {
                return $this->jsonResponse(['error' => 'invalid_input_per_1k'], 400);
            }
            $fields['input_per_1k'] = $body['input_per_1k'];
        }
        if (array_key_exists('output_per_1k', $body)) {
            if (!is_numeric($body['output_per_1k'])) {
                return $this->jsonResponse(['error' => 'invalid_output_per_1k'], 400);
            }
            $fields['output_per_1k'] = $body['output_per_1k'];
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
