// Typed client for console-api (Phalcon), PRD §9.1.
//
// Every export here is named exactly after the `operationId` of its path in
// contracts/openapi/console-api.yaml — see src/lib/api/README.md.

import { apiFetch } from './client';
import type {
	Agent,
	AgentVersion,
	ApiKey,
	ApiKeyCreated,
	Approval,
	Company,
	Connection,
	ConnectionSecretInput,
	DashboardSummary,
	Dataset,
	DatasetImportResult,
	DatasetInput,
	DatasetItem,
	Deployment,
	CreateEvalRunInput,
	EvalCostEstimate,
	EvalCostEstimateInput,
	EvalResult,
	EvalRun,
	EvalRunComparison,
	KbDocument,
	KbImportResultItem,
	KbSearchResult,
	KnowledgeBase,
	KnowledgeBaseInput,
	Me,
	Placeholder,
	Run,
	RuntimeTokenResponse,
	Skill,
	Tool,
	Workflow,
	WorkflowInput,
	WorkflowVersion
} from './types';

// ---- /me --------------------------------------------------------------

export function getMe(): Promise<Me> {
	return apiFetch<Me>('/me', { skipCompanyHeader: true });
}

// ---- /runtime-token -----------------------------------------------------

export function issueRuntimeToken(agentVersionId: string): Promise<RuntimeTokenResponse> {
	return apiFetch<RuntimeTokenResponse>('/runtime-token', {
		method: 'POST',
		body: { agent_version_id: agentVersionId }
	});
}

// ---- /connections ---------------------------------------------------------

export function listConnections(): Promise<Connection[]> {
	return apiFetch<Connection[]>('/connections');
}

export function createConnection(input: Partial<Connection>): Promise<Connection> {
	return apiFetch<Connection>('/connections', { method: 'POST', body: input });
}

export function getConnection(id: string): Promise<Connection> {
	return apiFetch<Connection>(`/connections/${id}`);
}

export function updateConnection(id: string, input: Partial<Connection>): Promise<Connection> {
	return apiFetch<Connection>(`/connections/${id}`, { method: 'PATCH', body: input });
}

export function deleteConnection(id: string): Promise<void> {
	return apiFetch<void>(`/connections/${id}`, { method: 'DELETE' });
}

export function putConnectionSecret(id: string, input: ConnectionSecretInput): Promise<void> {
	return apiFetch<void>(`/connections/${id}/secret`, { method: 'PUT', body: input });
}

export function testConnection(id: string): Promise<{ ok: boolean; detail: string }> {
	return apiFetch(`/connections/${id}/test`, { method: 'POST' });
}

// ---- /agents ----------------------------------------------------------

export function listAgents(): Promise<Agent[]> {
	return apiFetch<Agent[]>('/agents');
}

export function createAgent(input: Partial<Agent>): Promise<Agent> {
	return apiFetch<Agent>('/agents', { method: 'POST', body: input });
}

export function getAgent(id: string): Promise<Agent> {
	return apiFetch<Agent>(`/agents/${id}`);
}

export function updateAgent(id: string, input: Partial<Agent>): Promise<Agent> {
	return apiFetch<Agent>(`/agents/${id}`, { method: 'PATCH', body: input });
}

export function cloneAgent(id: string): Promise<Agent> {
	return apiFetch<Agent>(`/agents/${id}/clone`, { method: 'POST' });
}

export function archiveAgent(id: string): Promise<Agent> {
	return apiFetch<Agent>(`/agents/${id}/archive`, { method: 'POST' });
}

export function unarchiveAgent(id: string): Promise<Agent> {
	return apiFetch<Agent>(`/agents/${id}/unarchive`, { method: 'POST' });
}

export function exportAgent(id: string): Promise<string> {
	return apiFetch<string>(`/agents/${id}/export`);
}

export function importAgent(id: string, yaml: string): Promise<Agent> {
	return apiFetch<Agent>(`/agents/${id}/import`, {
		method: 'POST',
		rawBody: yaml,
		headers: { 'Content-Type': 'application/yaml' }
	});
}

export function listAgentVersions(id: string): Promise<AgentVersion[]> {
	return apiFetch<AgentVersion[]>(`/agents/${id}/versions`);
}

export function createAgentVersion(
	id: string,
	input: Partial<AgentVersion>
): Promise<AgentVersion> {
	return apiFetch<AgentVersion>(`/agents/${id}/versions`, { method: 'POST', body: input });
}

export function getAgentVersion(id: string, vid: string): Promise<AgentVersion> {
	return apiFetch<AgentVersion>(`/agents/${id}/versions/${vid}`);
}

export function updateAgentVersion(
	id: string,
	vid: string,
	input: Partial<AgentVersion>
): Promise<AgentVersion> {
	return apiFetch<AgentVersion>(`/agents/${id}/versions/${vid}`, {
		method: 'PATCH',
		body: input
	});
}

export function publishAgentVersion(id: string, vid: string): Promise<AgentVersion> {
	return apiFetch<AgentVersion>(`/agents/${id}/versions/${vid}/publish`, { method: 'POST' });
}

// ---- /deployments -----------------------------------------------------

export function listDeployments(): Promise<Deployment[]> {
	return apiFetch<Deployment[]>('/deployments');
}

export function createDeployment(input: Partial<Deployment>): Promise<Deployment> {
	return apiFetch<Deployment>('/deployments', { method: 'POST', body: input });
}

export function getDeployment(id: string): Promise<Deployment> {
	return apiFetch<Deployment>(`/deployments/${id}`);
}

export function updateDeployment(id: string, input: Partial<Deployment>): Promise<Deployment> {
	return apiFetch<Deployment>(`/deployments/${id}`, { method: 'PATCH', body: input });
}

export function deleteDeployment(id: string): Promise<void> {
	return apiFetch<void>(`/deployments/${id}`, { method: 'DELETE' });
}

/** Required on the first promote from this (staging) deployment; afterwards optional. */
export function promoteDeployment(id: string, targetDeploymentId?: string): Promise<Deployment> {
	return apiFetch<Deployment>(`/deployments/${id}/promote`, {
		method: 'POST',
		body: targetDeploymentId ? { target_deployment_id: targetDeploymentId } : undefined
	});
}

// ---- /api-keys ----------------------------------------------------------

export function listApiKeys(): Promise<ApiKey[]> {
	return apiFetch<ApiKey[]>('/api-keys');
}

export function createApiKey(input: Partial<ApiKey>): Promise<ApiKeyCreated> {
	return apiFetch<ApiKeyCreated>('/api-keys', { method: 'POST', body: input });
}

export function revokeApiKey(id: string): Promise<ApiKey> {
	return apiFetch<ApiKey>(`/api-keys/${id}/revoke`, { method: 'POST' });
}

// ---- /runs --------------------------------------------------------------

export interface ListRunsParams {
	agent_id?: string;
	deployment_id?: string;
	status?: string;
}

export function listRuns(params: ListRunsParams = {}): Promise<Run[]> {
	const query = new URLSearchParams(
		Object.entries(params).filter(([, v]) => v !== undefined) as [string, string][]
	).toString();
	return apiFetch<Run[]>(`/runs${query ? `?${query}` : ''}`);
}

export function getRun(id: string): Promise<Run> {
	return apiFetch<Run>(`/runs/${id}`);
}

export function getRunConversation(id: string): Promise<Placeholder> {
	return apiFetch<Placeholder>(`/runs/${id}/conversation`);
}

export function listRunGuardrailEvents(id: string): Promise<Placeholder[]> {
	return apiFetch<Placeholder[]>(`/runs/${id}/guardrail-events`);
}

// ---- /dashboard ---------------------------------------------------------

export function getDashboardSummary(): Promise<DashboardSummary> {
	return apiFetch<DashboardSummary>('/dashboard');
}

// ---- /tools -------------------------------------------------------------

export function listTools(): Promise<Tool[]> {
	return apiFetch<Tool[]>('/tools');
}

export function createTool(input: Partial<Tool>): Promise<Tool> {
	return apiFetch<Tool>('/tools', { method: 'POST', body: input });
}

export function updateTool(id: string, input: Partial<Tool>): Promise<Tool> {
	return apiFetch<Tool>(`/tools/${id}`, { method: 'PATCH', body: input });
}

export function deleteTool(id: string): Promise<void> {
	return apiFetch<void>(`/tools/${id}`, { method: 'DELETE' });
}

export function putToolSecret(id: string, input: ConnectionSecretInput): Promise<void> {
	return apiFetch<void>(`/tools/${id}/secret`, { method: 'PUT', body: input });
}

export function testTool(id: string): Promise<{ ok: boolean; detail: string }> {
	return apiFetch(`/tools/${id}/test`, { method: 'POST' });
}

// ---- /evals (PRD §6.7) ------------------------------------------------------
// listEvalRuns/getEvalRun/getEvalRunResults/compareEvalRuns hit console-api's own
// direct reads of runtime.eval_runs/logs.eval_results (no ai-runtime round trip);
// createEvalRun and estimateEvalCost forward to ai-runtime.

export function listEvalRuns(): Promise<EvalRun[]> {
	return apiFetch<EvalRun[]>('/evals');
}

export function createEvalRun(input: CreateEvalRunInput): Promise<EvalRun> {
	return apiFetch<EvalRun>('/evals', { method: 'POST', body: input });
}

export function estimateEvalCost(input: EvalCostEstimateInput): Promise<EvalCostEstimate> {
	return apiFetch<EvalCostEstimate>('/evals/estimate', { method: 'POST', body: input });
}

export function getEvalRun(id: string): Promise<EvalRun> {
	return apiFetch<EvalRun>(`/evals/${id}`);
}

export function getEvalRunResults(id: string): Promise<EvalResult[]> {
	return apiFetch<EvalResult[]>(`/evals/${id}/results`);
}

export function compareEvalRuns(a: string, b: string): Promise<EvalRunComparison> {
	return apiFetch<EvalRunComparison>(`/evals/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
}

// ---- /approvals (PRD §6.5) --------------------------------------------------

export function listApprovals(): Promise<Approval[]> {
	return apiFetch<Approval[]>('/approvals');
}

export function decideApproval(id: string, decision: 'approved' | 'rejected'): Promise<Approval> {
	return apiFetch<Approval>(`/approvals/${id}`, { method: 'POST', body: { decision } });
}

// ---- /kb (phase 2 - vector-only retrieval this round, PRD §6.6) -------------

export function listKnowledgeBases(): Promise<KnowledgeBase[]> {
	return apiFetch<KnowledgeBase[]>('/kb');
}

export function getKnowledgeBase(id: string): Promise<KnowledgeBase> {
	return apiFetch<KnowledgeBase>(`/kb/${id}`);
}

export function createKnowledgeBase(input: KnowledgeBaseInput): Promise<KnowledgeBase> {
	return apiFetch<KnowledgeBase>('/kb', { method: 'POST', body: input });
}

export function updateKnowledgeBase(id: string, input: KnowledgeBaseInput): Promise<KnowledgeBase> {
	return apiFetch<KnowledgeBase>(`/kb/${id}`, { method: 'PATCH', body: input });
}

export function deleteKnowledgeBase(id: string): Promise<void> {
	return apiFetch<void>(`/kb/${id}`, { method: 'DELETE' });
}

export function listKnowledgeBaseDocuments(id: string): Promise<KbDocument[]> {
	return apiFetch<KbDocument[]>(`/kb/${id}/documents`);
}

export function importKnowledgeBaseFiles(id: string, form: FormData): Promise<KbImportResultItem[]> {
	return apiFetch<KbImportResultItem[]>(`/kb/${id}/import`, { method: 'POST', rawBody: form });
}

export function exportKnowledgeBase(id: string): Promise<Blob> {
	return apiFetch<Blob>(`/kb/${id}/export`, { responseType: 'blob' });
}

export function searchKnowledgeBase(
	id: string,
	input: { query: string; top_k?: number }
): Promise<KbSearchResult> {
	return apiFetch<KbSearchResult>(`/kb/${id}/search`, { method: 'POST', body: input });
}

// ---- /datasets (PRD §6.7 - Evaluation, no ai-runtime involved) -------------

export function listDatasets(): Promise<Dataset[]> {
	return apiFetch<Dataset[]>('/datasets');
}

export function getDataset(id: string): Promise<Dataset> {
	return apiFetch<Dataset>(`/datasets/${id}`);
}

export function createDataset(input: DatasetInput): Promise<Dataset> {
	return apiFetch<Dataset>('/datasets', { method: 'POST', body: input });
}

export function updateDataset(id: string, input: DatasetInput): Promise<Dataset> {
	return apiFetch<Dataset>(`/datasets/${id}`, { method: 'PATCH', body: input });
}

export function deleteDataset(id: string): Promise<void> {
	return apiFetch<void>(`/datasets/${id}`, { method: 'DELETE' });
}

export function listDatasetItems(id: string): Promise<DatasetItem[]> {
	return apiFetch<DatasetItem[]>(`/datasets/${id}/items`);
}

export function importDatasetItems(id: string, form: FormData): Promise<DatasetImportResult> {
	return apiFetch<DatasetImportResult>(`/datasets/${id}/import`, { method: 'POST', rawBody: form });
}

// ---- /skills --------------------------------------------------------------

export function listSkills(): Promise<Skill[]> {
	return apiFetch<Skill[]>('/skills');
}

export interface SkillInput {
	name?: string;
	description?: string | null;
	status?: string;
	content?: string;
}

export function createSkill(input: SkillInput): Promise<Skill> {
	return apiFetch<Skill>('/skills', { method: 'POST', body: input });
}

export function updateSkill(id: string, input: SkillInput): Promise<Skill> {
	return apiFetch<Skill>(`/skills/${id}`, { method: 'PATCH', body: input });
}

export function publishSkill(id: string): Promise<Skill> {
	return apiFetch<Skill>(`/skills/${id}/publish`, { method: 'POST' });
}

// ---- /settings ------------------------------------------------------------

export function listCompanyUsers(): Promise<Placeholder[]> {
	return apiFetch<Placeholder[]>('/settings/users');
}

export function listEgressAllowlist(): Promise<Placeholder[]> {
	return apiFetch<Placeholder[]>('/settings/egress-allowlist');
}

export function createEgressAllowlistEntry(input: Placeholder): Promise<Placeholder> {
	return apiFetch<Placeholder>('/settings/egress-allowlist', { method: 'POST', body: input });
}

export function deleteEgressAllowlistEntry(id: string): Promise<void> {
	return apiFetch<void>(`/settings/egress-allowlist/${id}`, { method: 'DELETE' });
}

// ---- /companies (platform_admin) -------------------------------------------

export function listCompanies(): Promise<Company[]> {
	return apiFetch<Company[]>('/companies', { skipCompanyHeader: true });
}

export function createCompany(input: Partial<Company>): Promise<Company> {
	return apiFetch<Company>('/companies', { method: 'POST', body: input, skipCompanyHeader: true });
}

export function suspendCompany(id: string): Promise<Company> {
	return apiFetch<Company>(`/companies/${id}/suspend`, { method: 'POST', skipCompanyHeader: true });
}

export function activateCompany(id: string): Promise<Company> {
	return apiFetch<Company>(`/companies/${id}/activate`, {
		method: 'POST',
		skipCompanyHeader: true
	});
}

export function destroyCompanyDek(id: string): Promise<void> {
	return apiFetch<void>(`/companies/${id}/destroy-dek`, {
		method: 'POST',
		skipCompanyHeader: true
	});
}

export function listCompanyMembers(id: string): Promise<Placeholder[]> {
	return apiFetch<Placeholder[]>(`/companies/${id}/members`, { skipCompanyHeader: true });
}

// ---- /workflows (PRD §6.9, phase 4 - slice 1: CRUD + compile/validate, no execution/
// deployment exposure yet) ---------------------------------------------------

export function listWorkflows(): Promise<Workflow[]> {
	return apiFetch<Workflow[]>('/workflows');
}

export function createWorkflow(input: WorkflowInput): Promise<Workflow> {
	return apiFetch<Workflow>('/workflows', { method: 'POST', body: input });
}

export function getWorkflow(id: string): Promise<Workflow> {
	return apiFetch<Workflow>(`/workflows/${id}`);
}

export function updateWorkflow(id: string, input: WorkflowInput): Promise<Workflow> {
	return apiFetch<Workflow>(`/workflows/${id}`, { method: 'PATCH', body: input });
}

export function archiveWorkflow(id: string): Promise<Workflow> {
	return apiFetch<Workflow>(`/workflows/${id}/archive`, { method: 'POST' });
}

export function unarchiveWorkflow(id: string): Promise<Workflow> {
	return apiFetch<Workflow>(`/workflows/${id}/unarchive`, { method: 'POST' });
}

export function listWorkflowVersions(id: string): Promise<WorkflowVersion[]> {
	return apiFetch<WorkflowVersion[]>(`/workflows/${id}/versions`);
}

export function createWorkflowVersion(
	id: string,
	input: { spec: Record<string, unknown>; spec_version?: string }
): Promise<WorkflowVersion> {
	return apiFetch<WorkflowVersion>(`/workflows/${id}/versions`, { method: 'POST', body: input });
}

export function getWorkflowVersion(id: string, vid: string): Promise<WorkflowVersion> {
	return apiFetch<WorkflowVersion>(`/workflows/${id}/versions/${vid}`);
}

export function updateWorkflowVersion(
	id: string,
	vid: string,
	input: { spec?: Record<string, unknown>; spec_version?: string }
): Promise<WorkflowVersion> {
	return apiFetch<WorkflowVersion>(`/workflows/${id}/versions/${vid}`, {
		method: 'PATCH',
		body: input
	});
}

export function publishWorkflowVersion(id: string, vid: string): Promise<WorkflowVersion> {
	return apiFetch<WorkflowVersion>(`/workflows/${id}/versions/${vid}/publish`, { method: 'POST' });
}

export type {
	Agent,
	AgentVersion,
	ApiKey,
	ApiKeyCreated,
	Approval,
	Company,
	Connection,
	DashboardSummary,
	Deployment,
	KbDocument,
	KbImportResultItem,
	KbSearchResult,
	KbSearchResultItem,
	KnowledgeBase,
	KnowledgeBaseInput,
	Me,
	Run,
	RuntimeTokenResponse,
	Skill,
	Tool,
	Workflow,
	WorkflowInput,
	WorkflowVersion
} from './types';
