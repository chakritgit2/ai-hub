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
	Company,
	Connection,
	ConnectionSecretInput,
	DashboardSummary,
	Deployment,
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
	Tool
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

/** Phase 3. */
export function exportAgent(id: string): Promise<string> {
	return apiFetch<string>(`/agents/${id}/export`);
}

/** Phase 3. */
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

/** Phase 3. */
export function promoteDeployment(id: string): Promise<Deployment> {
	return apiFetch<Deployment>(`/deployments/${id}/promote`, { method: 'POST' });
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

export function testTool(id: string): Promise<{ ok: boolean; detail: string }> {
	return apiFetch(`/tools/${id}/test`, { method: 'POST' });
}

// ---- /datasets (phase 3) --------------------------------------------------

export function listDatasets(): Promise<Placeholder[]> {
	return apiFetch<Placeholder[]>('/datasets');
}

export function createDataset(input: Placeholder): Promise<Placeholder> {
	return apiFetch<Placeholder>('/datasets', { method: 'POST', body: input });
}

// ---- /evals (phase 3) -----------------------------------------------------

export function listEvalRuns(): Promise<Placeholder[]> {
	return apiFetch<Placeholder[]>('/evals');
}

export function createEvalRun(input: Placeholder): Promise<Placeholder> {
	return apiFetch<Placeholder>('/evals', { method: 'POST', body: input });
}

export function getEvalRun(id: string): Promise<Placeholder> {
	return apiFetch<Placeholder>(`/evals/${id}`);
}

export function compareEvalRuns(a: string, b: string): Promise<Placeholder> {
	return apiFetch<Placeholder>(`/evals/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
}

// ---- /approvals (phase 2) --------------------------------------------------

export function listApprovals(): Promise<Placeholder[]> {
	return apiFetch<Placeholder[]>('/approvals');
}

export function decideApproval(id: string, input: Placeholder): Promise<Placeholder> {
	return apiFetch<Placeholder>(`/approvals/${id}`, { method: 'POST', body: input });
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

export type {
	Agent,
	AgentVersion,
	ApiKey,
	ApiKeyCreated,
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
	Tool
} from './types';
