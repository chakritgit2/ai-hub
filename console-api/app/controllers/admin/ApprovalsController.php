<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use ConsoleApi\Services\ApprovalRequestInvalidException;
use ConsoleApi\Services\RuntimeClient;
use Phalcon\Db\Enum;
use Phalcon\Http\Response;
use RuntimeException;

/**
 * /approvals — human approval for `write`-access tool calls (PRD §6.5, §8.1
 * `tool_approvals` table). `listApprovals` reads `runtime.tool_approvals` **directly**,
 * same pattern `EvalsController::listEvalRuns` already established for `runtime.eval_runs`
 * - `console_app`'s read-only grant on this table (db/migrations/post/
 * 025_tool_approvals_console_grants.sql) is all it needs. `decideApproval` forwards to
 * ai-runtime instead (PRD §8.1: `console_app` only has SELECT on this table, never
 * INSERT/UPDATE) - same "console only reads, ai-runtime writes" split
 * ConnectionsController uses for secrets.
 *
 * Scoped to `developer` role for both methods (PRD §9.1's role table lists `/approvals`
 * once, not split by read/write like most other resources).
 */
class ApprovalsController extends ControllerBase
{
    private const APPROVAL_COLUMNS = 'id, company_id, run_id, tool_id, request, status, decided_by, decided_at, created_at';

    public function listApprovals(): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireWriteRole()) !== null) {
            return $error;
        }

        $rows = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchAll(
                'SELECT ' . self::APPROVAL_COLUMNS . ' FROM runtime.tool_approvals ORDER BY created_at DESC',
                Enum::FETCH_ASSOC
            )
        );

        return $this->jsonResponse(array_map([$this, 'formatApproval'], $rows));
    }

    public function decideApproval(string $id): Response
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
        $decision = $body['decision'] ?? null;
        if (!in_array($decision, ['approved', 'rejected'], true)) {
            return $this->jsonResponse(['error' => 'decision_must_be_approved_or_rejected'], 400);
        }

        $userId = $this->getAuthUser()['id'];

        /** @var RuntimeClient $runtimeClient */
        $runtimeClient = $this->getDI()->getShared('runtimeClient');
        try {
            $result = $runtimeClient->decideApproval($id, $decision, $userId);
        } catch (ApprovalRequestInvalidException $exception) {
            $error = $exception->getStatusCode() === 409 ? 'already_decided' : 'not_found';

            return $this->jsonResponse(['error' => $error, 'message' => $exception->getMessage()], $exception->getStatusCode());
        } catch (RuntimeException $exception) {
            return $this->jsonResponse(
                ['error' => 'decide_approval_failed', 'message' => $exception->getMessage()],
                502
            );
        }

        return $this->jsonResponse(['company_id' => $this->getCompanyId(), ...$result]);
    }

    /**
     * @param array<string, mixed> $row
     * @return array<string, mixed>
     */
    private function formatApproval(array $row): array
    {
        return [
            'id' => $row['id'],
            'company_id' => $row['company_id'],
            'run_id' => $row['run_id'],
            'tool_id' => $row['tool_id'],
            'request' => json_decode((string) $row['request'], true),
            'status' => $row['status'],
            'decided_by' => $row['decided_by'],
            'decided_at' => $row['decided_at'],
            'created_at' => $row['created_at'],
        ];
    }
}
