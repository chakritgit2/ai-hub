<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use ConsoleApi\Services\EvalEstimateInvalidException;
use ConsoleApi\Services\RuntimeClient;
use Phalcon\Db\Enum;
use Phalcon\Http\Response;
use RuntimeException;

/**
 * /evals — x-phase: 3 in the OpenAPI contract (PRD §8.1 `eval_runs`/`eval_results` tables).
 *
 * `estimateEvalCost` is implemented for real (forwards to ai-runtime's
 * `/internal/v1/evals/estimate`, which does the actual token counting + pricing lookup -
 * see `ai/app/services/eval_runner.py`). The rest (list/create/get/compare actual eval
 * runs) needs `runtime.eval_runs`/`logs.eval_results`, which don't exist yet - separate,
 * larger follow-up work.
 */
class EvalsController extends ControllerBase
{
    public function listEvalRuns(): Response
    {
        return $this->notImplemented();
    }

    public function createEvalRun(): Response
    {
        return $this->notImplemented();
    }

    public function getEvalRun(string $id): Response
    {
        return $this->notImplemented();
    }

    public function compareEvalRuns(): Response
    {
        return $this->notImplemented();
    }

    public function estimateEvalCost(): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $body = $this->request->getJsonRawBody(true) ?? [];

        $agentVersionId = $body['agent_version_id'] ?? null;
        if (!is_string($agentVersionId) || !$this->isUuid($agentVersionId)) {
            return $this->jsonResponse(['error' => 'agent_version_id_invalid'], 400);
        }

        $datasetId = $body['dataset_id'] ?? null;
        if (!is_string($datasetId) || !$this->isUuid($datasetId)) {
            return $this->jsonResponse(['error' => 'dataset_id_invalid'], 400);
        }

        if (!array_key_exists('metrics', $body)) {
            return $this->jsonResponse(['error' => 'metrics_required'], 400);
        }
        $metrics = $body['metrics'];
        if (!is_array($metrics) || array_filter($metrics, static fn ($metric) => !is_string($metric) || $metric === '')) {
            return $this->jsonResponse(['error' => 'metrics_must_be_array_of_non_empty_strings'], 400);
        }

        $judgeModel = $body['judge_model'] ?? '';
        if (!is_string($judgeModel)) {
            return $this->jsonResponse(['error' => 'judge_model_must_be_string'], 400);
        }

        if (!$this->rowExistsInCompany('console.datasets', $datasetId)) {
            return $this->jsonResponse(['error' => 'dataset_not_found'], 404);
        }
        if (!$this->rowExistsInCompany('console.agent_versions', $agentVersionId)) {
            return $this->jsonResponse(['error' => 'agent_version_not_found'], 404);
        }

        /** @var RuntimeClient $runtimeClient */
        $runtimeClient = $this->getDI()->getShared('runtimeClient');
        try {
            $result = $runtimeClient->estimateEvalCost(
                $agentVersionId,
                $datasetId,
                array_values($metrics),
                $judgeModel,
                (string) $this->getRoleForCurrentCompany()
            );
        } catch (EvalEstimateInvalidException $exception) {
            // ai-runtime's own resolve_dataset/resolve_and_compile re-checked both ids and
            // disagreed with the pre-checks above (TOCTOU: deleted in between) or found the
            // agent version doesn't currently compile - forward its real status/detail
            // instead of flattening it into a generic 502 (see RuntimeClient::requestWithStatus()).
            return $this->jsonResponse(['error' => 'estimate_invalid', 'message' => $exception->getMessage()], $exception->getStatusCode());
        } catch (RuntimeException $exception) {
            return $this->jsonResponse(
                ['error' => 'estimate_request_failed', 'message' => $exception->getMessage()],
                502
            );
        }

        return $this->jsonResponse($result);
    }

    /**
     * Confirms a row with this id belongs to the current company (RLS already scopes
     * this query, so "belongs to another company" and "doesn't exist" are
     * indistinguishable - both read as not-found, PRD §12/§7.7). `$table` must always be
     * a hardcoded literal from a call site here, never derived from request input - it's
     * interpolated directly into the query.
     */
    private function rowExistsInCompany(string $table, string $id): bool
    {
        if (!$this->isUuid($id)) {
            return false;
        }

        $row = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchOne("SELECT 1 FROM {$table} WHERE id = :id", Enum::FETCH_ASSOC, ['id' => $id])
        );

        return !empty($row);
    }
}
