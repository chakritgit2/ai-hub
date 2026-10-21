<?php

declare(strict_types=1);

use Phalcon\Mvc\Router;

/**
 * Registers every /admin/v1/* route from contracts/openapi/console-api.yaml onto $router,
 * 1:1 by operationId — the route's "action" is exactly the operationId, and it is exactly
 * the controller method name (no "Action" suffix, see services.php's dispatcher setup).
 *
 * Phase 2 / phase 3 operations (see x-phase in the OpenAPI doc) are registered exactly the
 * same way; their controllers simply return 501 {"error":"not_implemented"} for now.
 *
 * @return callable(Router): void
 */
return function (Router $router): void {
    $router->removeExtraSlashes(true);

    $admin = static function (string $method, string $pattern, string $controller, string $action) use ($router): void {
        $router
            ->add('/admin/v1' . $pattern, [
                'controller' => $controller,
                'action' => $action,
            ])
            ->via($method);
    };

    // --- /me ------------------------------------------------------------
    $admin('GET', '/me', 'Me', 'getMe');

    // --- /runtime-token ---------------------------------------------------
    $admin('POST', '/runtime-token', 'RuntimeToken', 'issueRuntimeToken');
    $admin('POST', '/workflow-runtime-token', 'RuntimeToken', 'issueWorkflowRuntimeToken');

    // --- /connections -------------------------------------------------------
    $admin('GET', '/connections', 'Connections', 'listConnections');
    $admin('POST', '/connections', 'Connections', 'createConnection');
    $admin('GET', '/connections/{id:[^/]+}', 'Connections', 'getConnection');
    $admin('PATCH', '/connections/{id:[^/]+}', 'Connections', 'updateConnection');
    $admin('DELETE', '/connections/{id:[^/]+}', 'Connections', 'deleteConnection');
    $admin('PUT', '/connections/{id:[^/]+}/secret', 'Connections', 'putConnectionSecret');
    $admin('POST', '/connections/{id:[^/]+}/test', 'Connections', 'testConnection');

    // --- /agents --------------------------------------------------------------
    $admin('GET', '/agents', 'Agents', 'listAgents');
    $admin('POST', '/agents', 'Agents', 'createAgent');
    $admin('GET', '/agents/{id:[^/]+}', 'Agents', 'getAgent');
    $admin('PATCH', '/agents/{id:[^/]+}', 'Agents', 'updateAgent');
    $admin('POST', '/agents/{id:[^/]+}/clone', 'Agents', 'cloneAgent');
    $admin('POST', '/agents/{id:[^/]+}/archive', 'Agents', 'archiveAgent');
    $admin('POST', '/agents/{id:[^/]+}/unarchive', 'Agents', 'unarchiveAgent');
    $admin('GET', '/agents/{id:[^/]+}/export', 'Agents', 'exportAgent');
    $admin('POST', '/agents/{id:[^/]+}/import', 'Agents', 'importAgent');
    $admin('GET', '/agents/{id:[^/]+}/versions', 'Agents', 'listAgentVersions');
    $admin('POST', '/agents/{id:[^/]+}/versions', 'Agents', 'createAgentVersion');
    $admin('GET', '/agents/{id:[^/]+}/versions/{vid:[^/]+}', 'Agents', 'getAgentVersion');
    $admin('PATCH', '/agents/{id:[^/]+}/versions/{vid:[^/]+}', 'Agents', 'updateAgentVersion');
    $admin('POST', '/agents/{id:[^/]+}/versions/{vid:[^/]+}/publish', 'Agents', 'publishAgentVersion');

    // --- /workflows (PRD §6.9, phase 4 - slice 1: CRUD + compile/validate, no
    // execution/deployment exposure yet) ------------------------------------
    $admin('GET', '/workflows', 'Workflows', 'listWorkflows');
    $admin('POST', '/workflows', 'Workflows', 'createWorkflow');
    $admin('GET', '/workflows/{id:[^/]+}', 'Workflows', 'getWorkflow');
    $admin('PATCH', '/workflows/{id:[^/]+}', 'Workflows', 'updateWorkflow');
    $admin('POST', '/workflows/{id:[^/]+}/archive', 'Workflows', 'archiveWorkflow');
    $admin('POST', '/workflows/{id:[^/]+}/unarchive', 'Workflows', 'unarchiveWorkflow');
    $admin('GET', '/workflows/{id:[^/]+}/versions', 'Workflows', 'listWorkflowVersions');
    $admin('POST', '/workflows/{id:[^/]+}/versions', 'Workflows', 'createWorkflowVersion');
    $admin('GET', '/workflows/{id:[^/]+}/versions/{vid:[^/]+}', 'Workflows', 'getWorkflowVersion');
    $admin('PATCH', '/workflows/{id:[^/]+}/versions/{vid:[^/]+}', 'Workflows', 'updateWorkflowVersion');
    $admin('POST', '/workflows/{id:[^/]+}/versions/{vid:[^/]+}/publish', 'Workflows', 'publishWorkflowVersion');

    // --- /deployments -----------------------------------------------------
    $admin('GET', '/deployments', 'Deployments', 'listDeployments');
    $admin('POST', '/deployments', 'Deployments', 'createDeployment');
    $admin('GET', '/deployments/{id:[^/]+}', 'Deployments', 'getDeployment');
    $admin('PATCH', '/deployments/{id:[^/]+}', 'Deployments', 'updateDeployment');
    $admin('DELETE', '/deployments/{id:[^/]+}', 'Deployments', 'deleteDeployment');
    $admin('POST', '/deployments/{id:[^/]+}/promote', 'Deployments', 'promoteDeployment');

    // --- /api-keys --------------------------------------------------------
    $admin('GET', '/api-keys', 'ApiKeys', 'listApiKeys');
    $admin('POST', '/api-keys', 'ApiKeys', 'createApiKey');
    $admin('POST', '/api-keys/{id:[^/]+}/revoke', 'ApiKeys', 'revokeApiKey');

    // --- /runs ------------------------------------------------------------
    $admin('GET', '/runs', 'Runs', 'listRuns');
    $admin('GET', '/runs/{id:[^/]+}', 'Runs', 'getRun');
    $admin('GET', '/runs/{id:[^/]+}/conversation', 'Runs', 'getRunConversation');
    $admin('GET', '/runs/{id:[^/]+}/guardrail-events', 'Runs', 'listRunGuardrailEvents');
    $admin('GET', '/runs/{id:[^/]+}/steps', 'Runs', 'listRunSteps');

    // --- /dashboard ---------------------------------------------------------
    $admin('GET', '/dashboard', 'Dashboard', 'getDashboardSummary');

    // --- /tools (x-phase: 2) ------------------------------------------------
    $admin('GET', '/tools', 'Tools', 'listTools');
    $admin('POST', '/tools', 'Tools', 'createTool');
    $admin('PATCH', '/tools/{id:[^/]+}', 'Tools', 'updateTool');
    $admin('DELETE', '/tools/{id:[^/]+}', 'Tools', 'deleteTool');
    $admin('PUT', '/tools/{id:[^/]+}/secret', 'Tools', 'putToolSecret');
    $admin('POST', '/tools/{id:[^/]+}/test', 'Tools', 'testTool');

    // --- /datasets (PRD §6.7/§8.2) ---------------------------------------------
    $admin('GET', '/datasets', 'Datasets', 'listDatasets');
    $admin('POST', '/datasets', 'Datasets', 'createDataset');
    $admin('GET', '/datasets/{id:[^/]+}', 'Datasets', 'getDataset');
    $admin('PATCH', '/datasets/{id:[^/]+}', 'Datasets', 'updateDataset');
    $admin('DELETE', '/datasets/{id:[^/]+}', 'Datasets', 'deleteDataset');
    $admin('GET', '/datasets/{id:[^/]+}/items', 'Datasets', 'listDatasetItems');
    $admin('POST', '/datasets/{id:[^/]+}/import', 'Datasets', 'importDatasetItems');

    // --- /evals (estimateEvalCost/createEvalRun/listEvalRuns/getEvalRun/
    // getEvalRunResults/compareEvalRuns all real now) ---------------------------
    // Registration order alone does NOT make the literal '/evals/compare' win over
    // '/evals/{id:[^/]+}' - confirmed live (a standalone Phalcon\Mvc\Router::handle()
    // script) that Phalcon 5.9's router resolved GET /evals/compare to
    // getEvalRun(id: 'compare') regardless of which was registered first, a real,
    // previously-undetected dead route (compareEvalRuns was a stub until this was
    // implemented, so nobody had tried it live before). Fixed at the source instead of
    // depending on route-registration order: {id} here is constrained to a hex/dash
    // shape that a real UUID has but a literal word like "compare" never does (it
    // contains letters outside a-f), so there is no longer any ambiguity for the
    // router to resolve either way.
    $admin('GET', '/evals/compare', 'Evals', 'compareEvalRuns');
    $admin('POST', '/evals/estimate', 'Evals', 'estimateEvalCost');
    $admin('GET', '/evals', 'Evals', 'listEvalRuns');
    $admin('POST', '/evals', 'Evals', 'createEvalRun');
    $admin('GET', '/evals/{id:[0-9a-fA-F-]+}', 'Evals', 'getEvalRun');
    $admin('GET', '/evals/{id:[0-9a-fA-F-]+}/results', 'Evals', 'getEvalRunResults');

    // --- /approvals (x-phase: 2) ----------------------------------------------
    $admin('GET', '/approvals', 'Approvals', 'listApprovals');
    $admin('POST', '/approvals/{id:[^/]+}', 'Approvals', 'decideApproval');

    // --- /kb (x-phase: 2) -----------------------------------------------------
    $admin('GET', '/kb', 'Kb', 'listKnowledgeBases');
    $admin('POST', '/kb', 'Kb', 'createKnowledgeBase');
    $admin('GET', '/kb/{id:[^/]+}', 'Kb', 'getKnowledgeBase');
    $admin('PATCH', '/kb/{id:[^/]+}', 'Kb', 'updateKnowledgeBase');
    $admin('DELETE', '/kb/{id:[^/]+}', 'Kb', 'deleteKnowledgeBase');
    $admin('GET', '/kb/{id:[^/]+}/documents', 'Kb', 'listKnowledgeBaseDocuments');
    $admin('POST', '/kb/{id:[^/]+}/import', 'Kb', 'importKnowledgeBaseFiles');
    $admin('GET', '/kb/{id:[^/]+}/export', 'Kb', 'exportKnowledgeBase');
    $admin('POST', '/kb/{id:[^/]+}/search', 'Kb', 'searchKnowledgeBase');

    // --- /skills (x-phase: 2) ---------------------------------------------------
    $admin('GET', '/skills', 'Skills', 'listSkills');
    $admin('POST', '/skills', 'Skills', 'createSkill');
    $admin('PATCH', '/skills/{id:[^/]+}', 'Skills', 'updateSkill');
    $admin('POST', '/skills/{id:[^/]+}/publish', 'Skills', 'publishSkill');

    // --- /settings --------------------------------------------------------------
    $admin('GET', '/settings/users', 'Settings', 'listCompanyUsers');
    $admin('GET', '/settings/egress-allowlist', 'Settings', 'listEgressAllowlist');
    $admin('POST', '/settings/egress-allowlist', 'Settings', 'createEgressAllowlistEntry');
    $admin('DELETE', '/settings/egress-allowlist/{id:[^/]+}', 'Settings', 'deleteEgressAllowlistEntry');

    // --- /companies (platform_admin only; no X-Company-Id) -----------------------
    $admin('GET', '/companies', 'Companies', 'listCompanies');
    $admin('POST', '/companies', 'Companies', 'createCompany');
    $admin('POST', '/companies/{id:[^/]+}/suspend', 'Companies', 'suspendCompany');
    $admin('POST', '/companies/{id:[^/]+}/activate', 'Companies', 'activateCompany');
    $admin('POST', '/companies/{id:[^/]+}/destroy-dek', 'Companies', 'destroyCompanyDek');
    $admin('GET', '/companies/{id:[^/]+}/members', 'Companies', 'listCompanyMembers');

    // --- Ops route, not part of the OpenAPI contract, never exposed via ingress ---
    $router
        ->add('/healthz', [
            'controller' => 'Health',
            'action' => 'index',
        ])
        ->via('GET');

    // --- Public JWKS (PRD §7.6) — no auth, no X-Company-Id; not under /admin/v1 ---
    $router
        ->add('/admin/.well-known/jwks.json', [
            'controller' => 'Jwks',
            'action' => 'getJwks',
        ])
        ->via('GET');
};
