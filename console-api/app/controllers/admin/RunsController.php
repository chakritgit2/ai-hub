<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Db\Enum;
use Phalcon\Http\Response;

/**
 * /runs — read-only run history (PRD §6.8/§6.9, §8.1 `logs.runs`/`logs.guardrail_events`,
 * `runtime.conversations` — all written by ai-runtime, read here directly since their RLS
 * policies have no `TO` clause and `console_app` now has a matching SELECT grant, PRD §8.1).
 */
class RunsController extends ControllerBase
{
    // Every logs.runs query in this controller aliases the table `r` and uses this exact
    // column list (qualified, since listRuns' agent_id filter joins console.agent_versions,
    // which also has id/company_id columns - ambiguous otherwise).
    private const RUN_COLUMNS = 'r.id, r.company_id, r.agent_version_id, r.deployment_id, r.conversation_id,
                                  r.source, r.status, r.agent_name, r.model, r.tokens_in, r.tokens_out,
                                  r.cost_usd, r.latency_ms, r.trace_id, r.created_at';

    public function listRuns(): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $agentId = $this->request->getQuery('agent_id');
        $deploymentId = $this->request->getQuery('deployment_id');
        $status = $this->request->getQuery('status');

        $joins = '';
        $conditions = [];
        $params = [];

        if (is_string($agentId) && $agentId !== '') {
            // logs.runs only has agent_version_id, not agent_id directly - the filter
            // the contract exposes is per-agent (across all its versions), so this joins
            // across schemas in one query (same physical database, different schemas;
            // console_app already has SELECT on console.agent_versions).
            $joins = 'JOIN console.agent_versions av ON av.id = r.agent_version_id';
            $conditions[] = 'av.agent_id = :agent_id';
            $params['agent_id'] = $agentId;
        }
        if (is_string($deploymentId) && $deploymentId !== '') {
            $conditions[] = 'r.deployment_id = :deployment_id';
            $params['deployment_id'] = $deploymentId;
        }
        if (is_string($status) && $status !== '') {
            $conditions[] = 'r.status = :status';
            $params['status'] = $status;
        }

        $where = $conditions === [] ? '' : 'WHERE ' . implode(' AND ', $conditions);

        $rows = $this->runInCompanyTransaction(
            fn ($db) => $db->fetchAll(
                'SELECT ' . self::RUN_COLUMNS . " FROM logs.runs r {$joins} {$where} ORDER BY r.created_at DESC",
                Enum::FETCH_ASSOC,
                $params
            )
        );

        return $this->jsonResponse(array_map([$this, 'formatRun'], $rows));
    }

    public function getRun(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $row = $this->findRun($id);
        if ($row === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        return $this->jsonResponse($this->formatRun($row));
    }

    public function getRunConversation(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        $run = $this->findRun($id);
        if ($run === null || $run['conversation_id'] === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        $conversation = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchOne(
                'SELECT id, deployment_id, source, external_user_id, last_message_at, expires_at
                 FROM runtime.conversations WHERE id = :id',
                Enum::FETCH_ASSOC,
                ['id' => $run['conversation_id']]
            )
        );
        if (empty($conversation)) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        return $this->jsonResponse([
            'id' => $conversation['id'],
            'deployment_id' => $conversation['deployment_id'],
            'source' => $conversation['source'],
            'external_user_id' => $conversation['external_user_id'],
            'last_message_at' => $conversation['last_message_at'],
            'expires_at' => $conversation['expires_at'],
        ]);
    }

    public function listRunGuardrailEvents(string $id): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }
        if ($this->findRun($id) === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        $rows = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchAll(
                'SELECT id, run_id, stage, "check", action, detail, created_at
                 FROM logs.guardrail_events WHERE run_id = :run_id ORDER BY created_at',
                Enum::FETCH_ASSOC,
                ['run_id' => $id]
            )
        );

        return $this->jsonResponse(array_map([$this, 'formatGuardrailEvent'], $rows));
    }

    /**
     * @return array<string, mixed>|null
     */
    private function findRun(string $id): ?array
    {
        if (!$this->isUuid($id)) {
            return null;
        }

        $row = $this->runInCompanyTransaction(
            fn ($db) => $db->fetchOne(
                'SELECT ' . self::RUN_COLUMNS . ' FROM logs.runs r WHERE r.id = :id',
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
    private function formatRun(array $row): array
    {
        return [
            'id' => $row['id'],
            'company_id' => $row['company_id'],
            'agent_version_id' => $row['agent_version_id'],
            'deployment_id' => $row['deployment_id'],
            'conversation_id' => $row['conversation_id'],
            'source' => $row['source'],
            'status' => $row['status'],
            'agent_name' => $row['agent_name'],
            'model' => $row['model'],
            'tokens_in' => $row['tokens_in'] !== null ? (int) $row['tokens_in'] : null,
            'tokens_out' => $row['tokens_out'] !== null ? (int) $row['tokens_out'] : null,
            'cost_usd' => $row['cost_usd'] !== null ? (float) $row['cost_usd'] : null,
            'latency_ms' => $row['latency_ms'] !== null ? (int) $row['latency_ms'] : null,
            'trace_id' => $row['trace_id'],
            'created_at' => $row['created_at'],
        ];
    }

    /**
     * @param array<string, mixed> $row
     * @return array<string, mixed>
     */
    private function formatGuardrailEvent(array $row): array
    {
        return [
            'id' => $row['id'],
            'run_id' => $row['run_id'],
            'stage' => $row['stage'],
            'check' => $row['check'],
            'action' => $row['action'],
            'detail' => json_decode((string) $row['detail'], true),
            'created_at' => $row['created_at'],
        ];
    }
}
