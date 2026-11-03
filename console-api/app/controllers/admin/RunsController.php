<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Db\Enum;
use Phalcon\Http\Response;

/**
 * /runs — read-only run history (PRD §6.8/§6.9, §8.1 `logs.runs`/`logs.guardrail_events`/
 * `logs.run_steps`, `runtime.conversations` — all written by ai-runtime, read here directly
 * since their RLS policies have no `TO` clause and `console_app` now has a matching SELECT
 * grant, PRD §8.1).
 */
class RunsController extends ControllerBase
{
    // Every logs.runs query in this controller aliases the table `r` and uses this exact
    // column list (qualified, since listRuns' agent_id filter joins console.agent_versions,
    // which also has id/company_id columns - ambiguous otherwise). `error` and
    // `guardrail_cost_usd` are plain technical/billing detail (provider exception text,
    // a cost figure) - unlike raw `input`/`output` (may hold customer PII, see
    // replayRun()'s docblock for why those two alone stay gated), nothing here is more
    // sensitive than what formatRunStep() below already exposes to any member.
    // `workflow_name` comes from RUN_FROM's LEFT JOINs, not logs.runs itself - null for
    // every agent run (workflow_version_id is null), same as agent_name/model are null
    // for every workflow run today.
    private const RUN_COLUMNS = 'r.id, r.company_id, r.agent_version_id, r.workflow_version_id, r.deployment_id,
                                  r.conversation_id, r.source, r.status, r.agent_name, r.model, r.tokens_in,
                                  r.tokens_out, r.cost_usd, r.guardrail_cost_usd, r.latency_ms, r.trace_id, r.error,
                                  r.created_at, w.name AS workflow_name';

    // LEFT (not INNER) since workflow_version_id is null for every agent run - those rows
    // must still come back, just with workflow_name null.
    private const RUN_FROM = 'FROM logs.runs r
                               LEFT JOIN console.workflow_versions wv ON wv.id = r.workflow_version_id
                               LEFT JOIN console.workflows w ON w.id = wv.workflow_id';

    /**
     * Ninth menu in the "each menu shows only its own tenant's data" pilot (option A, see
     * ControllerBase::poolAcrossTenant()'s docblock for the full reasoning) - same pattern
     * applied verbatim: an optional X-Tenant-Id header pools runs across every company
     * under that tenant the caller actually belongs to, instead of the usual single
     * X-Company-Id-scoped company. The agent_id/deployment_id/status query filters are
     * built once by buildRunFilters() below and applied identically whichever company a
     * row came from - $query is the same closure either path ends up running.
     */
    public function listRuns(): Response
    {
        [$joins, $where, $params] = $this->buildRunFilters();
        $query = fn ($db) => $db->fetchAll(
            'SELECT ' . self::RUN_COLUMNS . ' ' . self::RUN_FROM . " {$joins} {$where} ORDER BY r.created_at DESC",
            Enum::FETCH_ASSOC,
            $params
        );

        $tenantId = $_SERVER['HTTP_X_TENANT_ID'] ?? null;
        if ($tenantId !== null && $tenantId !== '') {
            return $this->jsonResponse(array_map(
                [$this, 'formatRun'],
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

        return $this->jsonResponse(array_map([$this, 'formatRun'], $rows));
    }

    /**
     * @return array{0: string, 1: string, 2: array<string, string>}
     */
    private function buildRunFilters(): array
    {
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

        return [$joins, $where, $params];
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

    /**
     * Returns this run's raw `input` plus the agent/version it ran against, so the console
     * can pre-fill a Playground session for a human to review, edit and resend ("Replay").
     * RUN_COLUMNS (used by every other endpoint in this controller) deliberately omits
     * `input`/`output` - this and getRunContent() below are the only two places raw run
     * content (which may hold a customer's PII) becomes visible, so both are gated by
     * requireWriteRole() rather than requireMembership() (same write-role boundary PRD
     * §7.2 draws everywhere else: viewer is read-only). POST (not GET) so AuditMiddleware's
     * existing "one row per successful mutating request" logging records who replayed
     * which run and when, with no new audit-log code needed.
     */
    public function replayRun(string $id): Response
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

        $row = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchOne(
                'SELECT input, agent_version_id FROM logs.runs WHERE id = :id',
                Enum::FETCH_ASSOC,
                ['id' => $id]
            )
        );
        if (empty($row) || $row['agent_version_id'] === null) {
            // agent_version_id is null for a workflow run - Playground only replays
            // single-agent runs, so there's nothing to pre-fill it with.
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        $agentId = $this->agentIdForVersion($row['agent_version_id']);
        if ($agentId === null) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        return $this->jsonResponse([
            'input' => $row['input'],
            'agent_id' => $agentId,
            'agent_version_id' => $row['agent_version_id'],
        ]);
    }

    /**
     * Read-only counterpart to replayRun() above, for viewing a run's raw input/output
     * directly on the Run detail page instead of having to leave for Playground just to
     * see what was asked/answered - same requireWriteRole() PII boundary and same POST
     * (not GET) choice, for the same AuditMiddleware audit-trail reason, as replayRun()'s
     * own docblock explains. Works for workflow runs too (replayRun() doesn't - Playground
     * has no workflow-replay path), since viewing content has nothing to do with Playground.
     */
    public function getRunContent(string $id): Response
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

        $row = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchOne(
                'SELECT input, output FROM logs.runs WHERE id = :id',
                Enum::FETCH_ASSOC,
                ['id' => $id]
            )
        );
        if (empty($row)) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        return $this->jsonResponse(['input' => $row['input'], 'output' => $row['output']]);
    }

    /**
     * Same lookup as DeploymentsController::agentIdForVersion - kept local rather than
     * shared, matching this codebase's convention of controller-local helpers.
     */
    private function agentIdForVersion(string $agentVersionId): ?string
    {
        $row = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchOne(
                'SELECT agent_id FROM console.agent_versions WHERE id = :id',
                Enum::FETCH_ASSOC,
                ['id' => $agentVersionId]
            )
        );

        return empty($row) ? null : $row['agent_id'];
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

    public function listRunSteps(string $id): Response
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
                'SELECT id, run_id, step_index, step_type, name, status, input, output,
                        tokens_in, tokens_out, cost_usd, latency_ms, error, detail, created_at
                 FROM logs.run_steps WHERE run_id = :run_id ORDER BY step_index',
                Enum::FETCH_ASSOC,
                ['run_id' => $id]
            )
        );

        return $this->jsonResponse(array_map([$this, 'formatRunStep'], $rows));
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
                'SELECT ' . self::RUN_COLUMNS . ' ' . self::RUN_FROM . ' WHERE r.id = :id',
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
            'workflow_version_id' => $row['workflow_version_id'],
            'workflow_name' => $row['workflow_name'],
            'deployment_id' => $row['deployment_id'],
            'conversation_id' => $row['conversation_id'],
            'source' => $row['source'],
            'status' => $row['status'],
            'agent_name' => $row['agent_name'],
            'model' => $row['model'],
            'tokens_in' => $row['tokens_in'] !== null ? (int) $row['tokens_in'] : null,
            'tokens_out' => $row['tokens_out'] !== null ? (int) $row['tokens_out'] : null,
            'cost_usd' => $row['cost_usd'] !== null ? (float) $row['cost_usd'] : null,
            'guardrail_cost_usd' => $row['guardrail_cost_usd'] !== null ? (float) $row['guardrail_cost_usd'] : null,
            'latency_ms' => $row['latency_ms'] !== null ? (int) $row['latency_ms'] : null,
            'trace_id' => $row['trace_id'],
            'error' => $row['error'],
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

    /**
     * @param array<string, mixed> $row
     * @return array<string, mixed>
     */
    private function formatRunStep(array $row): array
    {
        return [
            'id' => $row['id'],
            'run_id' => $row['run_id'],
            'step_index' => (int) $row['step_index'],
            'step_type' => $row['step_type'],
            'name' => $row['name'],
            'status' => $row['status'],
            'input' => $row['input'],
            'output' => $row['output'],
            'tokens_in' => $row['tokens_in'] !== null ? (int) $row['tokens_in'] : null,
            'tokens_out' => $row['tokens_out'] !== null ? (int) $row['tokens_out'] : null,
            'cost_usd' => $row['cost_usd'] !== null ? (float) $row['cost_usd'] : null,
            'latency_ms' => $row['latency_ms'] !== null ? (int) $row['latency_ms'] : null,
            'error' => $row['error'],
            'detail' => json_decode((string) $row['detail'], true),
            'created_at' => $row['created_at'],
        ];
    }
}
