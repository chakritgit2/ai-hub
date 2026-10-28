<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use ConsoleApi\Services\EvalRequestInvalidException;
use ConsoleApi\Services\RuntimeClient;
use Phalcon\Db\Enum;
use Phalcon\Http\Response;
use RuntimeException;

/**
 * /evals — PRD §6.7/§8.1 (`eval_runs`/`eval_results` tables).
 *
 * `estimateEvalCost`/`createEvalRun` forward to ai-runtime (the former just computes a
 * number, the latter needs ai-runtime to enqueue the real ARQ job - see
 * `ai/app/services/eval_runner.py`/`main_runtime.py`). `listEvalRuns`/`getEvalRun`/
 * `getEvalRunResults`/`compareEvalRuns` read `runtime.eval_runs`/`logs.eval_results`
 * **directly** instead - same pattern `RunsController` already established for
 * `logs.runs`/`runtime.conversations`: those tables' own `company_isolation` RLS
 * policies have no `TO` clause, so they already apply to any role with a table-level
 * SELECT grant, and `console_app` has exactly that grant (`db/migrations/post/
 * 020_eval_runs_results_console_grants.sql`).
 */
class EvalsController extends ControllerBase
{
    private const EVAL_RUN_COLUMNS = 'id, company_id, agent_version_id, dataset_id, role, metrics,
                                       judge_model, status, est_cost_usd, summary, error,
                                       created_at, updated_at';

    /**
     * Twelfth and final menu in the "each menu shows only its own tenant's data" pilot
     * (option A, see ControllerBase::poolAcrossTenant()'s docblock for the full
     * reasoning) - same pattern applied verbatim: an optional X-Tenant-Id header pools
     * eval runs across every company under that tenant the caller actually belongs to,
     * instead of the usual single X-Company-Id-scoped company. The
     * agent_version_id/dataset_id/status query filters are built once by
     * buildEvalRunFilters() below and applied identically whichever company a row came
     * from - $query is the same closure either path ends up running (same approach as
     * RunsController::listRuns()).
     */
    public function listEvalRuns(): Response
    {
        [$where, $params] = $this->buildEvalRunFilters();
        $query = fn ($db) => $db->fetchAll(
            'SELECT ' . self::EVAL_RUN_COLUMNS . " FROM runtime.eval_runs {$where} ORDER BY created_at DESC",
            Enum::FETCH_ASSOC,
            $params
        );

        $tenantId = $_SERVER['HTTP_X_TENANT_ID'] ?? null;
        if ($tenantId !== null && $tenantId !== '') {
            return $this->jsonResponse(array_map(
                [$this, 'formatEvalRun'],
                $this->poolAcrossTenant($tenantId, $query)
            ));
        }

        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $rows = $this->runInCompanyTransaction($query);

        return $this->jsonResponse(array_map([$this, 'formatEvalRun'], $rows));
    }

    /**
     * @return array{0: string, 1: array<string, string>}
     */
    private function buildEvalRunFilters(): array
    {
        $agentVersionId = $this->request->getQuery('agent_version_id');
        $datasetId = $this->request->getQuery('dataset_id');
        $status = $this->request->getQuery('status');

        $conditions = [];
        $params = [];

        if (is_string($agentVersionId) && $agentVersionId !== '') {
            $conditions[] = 'agent_version_id = :agent_version_id';
            $params['agent_version_id'] = $agentVersionId;
        }
        if (is_string($datasetId) && $datasetId !== '') {
            $conditions[] = 'dataset_id = :dataset_id';
            $params['dataset_id'] = $datasetId;
        }
        if (is_string($status) && $status !== '') {
            $conditions[] = 'status = :status';
            $params['status'] = $status;
        }

        $where = $conditions === [] ? '' : 'WHERE ' . implode(' AND ', $conditions);

        return [$where, $params];
    }

    public function createEvalRun(): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        // Not requireMembership() - this triggers a real, billable agent run per
        // dataset item (PRD §7.2: viewer is read-only everywhere), same write-role gate
        // every mutating DatasetsController method already uses.
        if (($error = $this->requireWriteRole()) !== null) {
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
        if (!is_array($metrics) || $metrics === [] || array_filter($metrics, static fn ($metric) => !is_string($metric) || $metric === '')) {
            // Unlike estimateEvalCost (a pure-math preview, harmless either way), an empty
            // list here would still run every dataset item through the real agent and
            // score nothing - burning real LLM spend for a result nobody can use.
            return $this->jsonResponse(['error' => 'metrics_must_be_non_empty_array_of_non_empty_strings'], 400);
        }

        // Rejected here, before an eval_runs row is even created or a job enqueued - not
        // silently skipped or left for ai-runtime to 422 on. See eval_runner.py's module
        // docstring for why these two aren't executable yet.
        $unsupported = array_values(array_intersect($metrics, ['llm_evaluator', 'python_evaluator']));
        if ($unsupported !== []) {
            return $this->jsonResponse(['error' => 'metric_not_executable_yet', 'metrics' => $unsupported], 422);
        }

        $judgeModel = $body['judge_model'] ?? null;
        if ($judgeModel !== null && !is_string($judgeModel)) {
            return $this->jsonResponse(['error' => 'judge_model_must_be_string'], 400);
        }

        $estCostUsd = $body['est_cost_usd'] ?? null;
        if ($estCostUsd !== null && !is_numeric($estCostUsd)) {
            return $this->jsonResponse(['error' => 'est_cost_usd_must_be_numeric'], 400);
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
            $result = $runtimeClient->createEvalRun(
                $agentVersionId,
                $datasetId,
                array_values($metrics),
                $judgeModel,
                (string) $this->getRoleForCurrentCompany(),
                $estCostUsd !== null ? (float) $estCostUsd : null
            );
        } catch (EvalRequestInvalidException $exception) {
            // ai-runtime's own checks caught something console-api doesn't pre-validate
            // (judge_model required for an LLM-based metric, or a TOCTOU-deleted
            // dataset/agent_version) - forward the real status/detail, not a flattened 502.
            return $this->jsonResponse(['error' => 'create_eval_run_invalid', 'message' => $exception->getMessage()], $exception->getStatusCode());
        } catch (RuntimeException $exception) {
            return $this->jsonResponse(
                ['error' => 'create_eval_run_failed', 'message' => $exception->getMessage()],
                502
            );
        }

        return $this->jsonResponse($result, 201);
    }

    public function cancelEvalRun(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        // Not requireMembership() - same write-role gate createEvalRun() uses, since this
        // changes a run's state (PRD §7.2: viewer is read-only everywhere).
        if (($error = $this->requireWriteRole()) !== null) {
            return $error;
        }
        if (!$this->isUuid($id)) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        /** @var RuntimeClient $runtimeClient */
        $runtimeClient = $this->getDI()->getShared('runtimeClient');
        try {
            $result = $runtimeClient->cancelEvalRun($id);
        } catch (EvalRequestInvalidException $exception) {
            $error = $exception->getStatusCode() === 409 ? 'already_terminal' : 'not_found';

            return $this->jsonResponse(['error' => $error, 'message' => $exception->getMessage()], $exception->getStatusCode());
        } catch (RuntimeException $exception) {
            return $this->jsonResponse(
                ['error' => 'cancel_eval_run_failed', 'message' => $exception->getMessage()],
                502
            );
        }

        return $this->jsonResponse($result);
    }

    public function getEvalRun(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $row = $this->findEvalRun($id);
        if ($row === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        return $this->jsonResponse($this->formatEvalRun($row));
    }

    public function getEvalRunResults(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }
        if ($this->findEvalRun($id) === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        $rows = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchAll(
                'SELECT id, eval_run_id, dataset_item_id, run_id, scores, reasons, error, created_at
                 FROM logs.eval_results WHERE eval_run_id = :eval_run_id ORDER BY created_at',
                Enum::FETCH_ASSOC,
                ['eval_run_id' => $id]
            )
        );

        return $this->jsonResponse(array_map([$this, 'formatEvalResult'], $rows));
    }

    public function compareEvalRuns(): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $idA = $this->request->getQuery('a');
        $idB = $this->request->getQuery('b');
        if (!is_string($idA) || !$this->isUuid($idA) || !is_string($idB) || !$this->isUuid($idB)) {
            return $this->jsonResponse(['error' => 'a_and_b_must_be_uuids'], 400);
        }

        $rowA = $this->findEvalRun($idA);
        if ($rowA === null) {
            return $this->jsonResponse(['error' => 'a_not_found'], 404);
        }
        $rowB = $this->findEvalRun($idB);
        if ($rowB === null) {
            return $this->jsonResponse(['error' => 'b_not_found'], 404);
        }

        $runA = $this->formatEvalRun($rowA);
        $runB = $this->formatEvalRun($rowB);

        // Diffs each run's already-computed summary.mean_scores (written once by
        // run_eval on completion) - no re-scoring, no logs.eval_results read. A run
        // that hasn't reached "completed" yet just contributes no metrics here; its
        // own `status` (in run_a/run_b) tells the caller why.
        $meanScoresA = $runA['summary']['mean_scores'] ?? [];
        $meanScoresB = $runB['summary']['mean_scores'] ?? [];
        $metricNames = array_unique([...array_keys($meanScoresA), ...array_keys($meanScoresB)]);
        sort($metricNames);

        $metrics = array_map(static function (string $metric) use ($meanScoresA, $meanScoresB): array {
            $aMean = $meanScoresA[$metric] ?? null;
            $bMean = $meanScoresB[$metric] ?? null;
            return [
                'metric' => $metric,
                'a_mean' => $aMean,
                'b_mean' => $bMean,
                // Only a real number when BOTH sides are known - same "don't synthesize
                // a confident number from a partially-unknown pair" rule eval_runner.py's
                // total_cost_usd handling already established, not "0 if unknown".
                'delta' => ($aMean !== null && $bMean !== null) ? $bMean - $aMean : null,
            ];
        }, $metricNames);

        return $this->jsonResponse(['run_a' => $runA, 'run_b' => $runB, 'metrics' => $metrics]);
    }

    /**
     * @return array<string, mixed>|null
     */
    private function findEvalRun(string $id): ?array
    {
        if (!$this->isUuid($id)) {
            return null;
        }

        $row = $this->runInCompanyTransaction(
            fn ($db) => $db->fetchOne(
                'SELECT ' . self::EVAL_RUN_COLUMNS . ' FROM runtime.eval_runs WHERE id = :id',
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
    private function formatEvalRun(array $row): array
    {
        return [
            'id' => $row['id'],
            'company_id' => $row['company_id'],
            'agent_version_id' => $row['agent_version_id'],
            'dataset_id' => $row['dataset_id'],
            'role' => $row['role'],
            'metrics' => json_decode((string) $row['metrics'], true),
            'judge_model' => $row['judge_model'],
            'status' => $row['status'],
            'est_cost_usd' => $row['est_cost_usd'] !== null ? (float) $row['est_cost_usd'] : null,
            'summary' => $row['summary'] !== null ? json_decode((string) $row['summary'], true) : null,
            'error' => $row['error'],
            'created_at' => $row['created_at'],
            'updated_at' => $row['updated_at'],
        ];
    }

    /**
     * @param array<string, mixed> $row
     * @return array<string, mixed>
     */
    private function formatEvalResult(array $row): array
    {
        return [
            'id' => $row['id'],
            'eval_run_id' => $row['eval_run_id'],
            'dataset_item_id' => $row['dataset_item_id'],
            'run_id' => $row['run_id'],
            'scores' => $row['scores'] !== null ? json_decode((string) $row['scores'], true) : null,
            'reasons' => $row['reasons'] !== null ? json_decode((string) $row['reasons'], true) : null,
            'error' => $row['error'],
            'created_at' => $row['created_at'],
        ];
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
        } catch (EvalRequestInvalidException $exception) {
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
