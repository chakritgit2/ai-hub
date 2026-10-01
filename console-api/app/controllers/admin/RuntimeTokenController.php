<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use ConsoleApi\Services\TokenIssuer;
use Phalcon\Db\Enum;
use Phalcon\Http\Response;

/**
 * POST /runtime-token — issues a 5-minute Playground runtime JWT (PRD §4.4-B, §7.6).
 * Backed by ConsoleApi\Services\TokenIssuer.
 */
class RuntimeTokenController extends ControllerBase
{
    public function issueRuntimeToken(): Response
    {
        $companyId = $this->getCompanyId();
        if ($companyId === null) {
            return $this->jsonResponse(['error' => 'company_id_required'], 400);
        }

        $role = $this->getRoleForCurrentCompany();
        if ($role === null) {
            // Same shape as a company that doesn't exist, per PRD §7.3/§12 — the caller
            // learns nothing about whether $companyId is real, just that they can't use it.
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        $body = $this->request->getJsonRawBody(true) ?? [];
        $agentVersionId = $body['agent_version_id'] ?? null;
        if (!is_string($agentVersionId) || !$this->isUuid($agentVersionId)) {
            return $this->jsonResponse(['error' => 'agent_version_id_required'], 400);
        }

        if (!$this->agentVersionExistsInCompany($agentVersionId)) {
            return $this->jsonResponse(['error' => 'not_found'], 404);
        }

        /** @var TokenIssuer $tokenIssuer */
        $tokenIssuer = $this->getDI()->getShared('tokenIssuer');
        $result = $tokenIssuer->issueRuntimeToken($companyId, $this->getAuthUser()['id'], $role, $agentVersionId);

        return $this->jsonResponse(['token' => $result['token'], 'expires_in' => $result['expires_in']]);
    }

    /**
     * agent_versions is RLS-protected (PRD §7.3) — the lookup must run with
     * app.company_id set, so a version belonging to another company looks identical to
     * one that doesn't exist at all.
     */
    private function agentVersionExistsInCompany(string $agentVersionId): bool
    {
        $row = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchOne(
                'SELECT id FROM console.agent_versions WHERE id = :id',
                Enum::FETCH_ASSOC,
                ['id' => $agentVersionId]
            )
        );

        // fetchOne()'s declared return type is `array`, not `array|false` — see the
        // same note in AuthService::syncUser().
        return !empty($row);
    }
}
