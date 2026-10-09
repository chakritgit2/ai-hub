// Types mirroring components.schemas in contracts/openapi/console-api.yaml.
// Hand-written for now; see src/lib/api/README.md for the plan to generate these.

export interface Company {
	id: string;
	code: string;
	name: string;
	external_ref?: string;
	status: 'active' | 'suspended';
	monthly_budget_usd?: number;
}

export interface Me {
	id: string;
	email: string;
	is_platform_admin: boolean;
	companies: Array<{ company_id: string; role: 'admin' | 'developer' | 'viewer' }>;
}

export interface RuntimeTokenResponse {
	token: string;
	expires_in: number;
}

export interface Connection {
	id: string;
	company_id: string;
	name: string;
	type: string;
	api_base?: string;
	masked_hint?: string;
	max_concurrency?: number;
}

export interface ConnectionSecretInput {
	secret: string;
}

// CRUD + a standalone connectivity test only (PRD §6.5) — not yet wired into the agent
// spec/compiler, so no agent can call one during a run.
export interface Tool {
	id: string;
	company_id: string;
	name: string;
	kind: 'http' | 'builtin' | 'python';
	access_level: 'read' | 'write';
	auth_mode: 'service' | 'delegated';
	audience?: string | null;
	// kind-specific: for http, url/method/headers/description/input_schema.
	config: Record<string, unknown>;
	enabled: boolean;
}

export interface SkillVersion {
	id: string;
	version_no: number;
	content: string;
	content_hash: string;
	has_scripts: boolean;
	is_published: boolean;
	published_at?: string | null;
}

// CRUD + versioning only (PRD §6.6a) — not yet wired into the agent spec/compiler, so no
// agent can load one via SkillsTool during a run.
export interface Skill {
	id: string;
	company_id: string;
	name: string;
	description?: string | null;
	status: string;
	latest_version: SkillVersion;
}

// Vector-only retrieval this round (PRD §6.6) - retrieval_mode: 'hybrid' is accepted but
// rejected (422) at index/search time until hybrid retrieval ships.
export interface KnowledgeBase {
	id: string;
	company_id: string;
	name: string;
	embedder_connection_id: string;
	chunk_size: number;
	chunk_overlap: number;
	retrieval_mode: 'vector' | 'hybrid';
	alpha: number;
	okf_field_map: Record<string, unknown>;
	created_at: string;
	updated_at: string;
}

export interface KnowledgeBaseInput {
	name?: string;
	embedder_connection_id?: string;
	chunk_size?: number;
	chunk_overlap?: number;
	retrieval_mode?: 'vector' | 'hybrid';
	alpha?: number;
	okf_field_map?: Record<string, unknown>;
}

export interface KbImportResultItem {
	document_id: string;
	path: string;
	status: 'queued' | 'processing' | 'ready' | 'failed';
}

export interface KbDocument {
	id: string;
	path: string;
	category: string | null;
	status: 'queued' | 'processing' | 'ready' | 'failed';
	chunk_count: number;
	error: string | null;
}

export interface KbSearchResultItem {
	content: string;
	metadata: Record<string, unknown>;
	score: number | null;
}

export interface KbSearchResult {
	results: KbSearchResultItem[];
}

export interface Agent {
	id: string;
	company_id: string;
	name: string;
	description?: string;
	status: string;
	archived_at?: string | null;
	// Convenience fields surfaced from the latest identity (PRD §6.1a) for the catalog view.
	display_name?: string;
	role?: string;
	owner?: string;
	languages?: string[];
}

export interface AgentIdentity {
	display_name: string;
	name: string;
	owner: string;
	role: string;
	languages: string[];
	persona?: string;
	tags?: string[];
	responsibilities?: string;
	in_scope?: string;
	out_of_scope?: string;
	handoff?: string;
	instructions?: string;
}

export interface ModelSpec {
	connection_id: string;
	model: string;
	temperature?: number;
	max_tokens?: number;
}

// Mirrors ai/app/services/agent_spec.py's GuardrailCheckSpec/GuardrailsSpec exactly.
// valid_json/valid_choices are output-only (the compiler rejects them in `input`); mask is
// invalid for max_length/valid_json/valid_choices (nothing identifiable to redact).
export type GuardrailCheckType = 'max_length' | 'regex_blocklist' | 'pii' | 'valid_json' | 'valid_choices';
export type GuardrailAction = 'block' | 'mask' | 'flag';

export interface GuardrailCheckSpec {
	check_type: GuardrailCheckType;
	action: GuardrailAction;
	on_error: 'block' | 'flag';
	fallback_message?: string | null;
	// check_type === 'max_length'
	max_length?: number | null;
	// check_type === 'regex_blocklist'
	pattern?: string | null;
	// check_type === 'valid_choices'
	choices?: string[] | null;
}

export interface GuardrailsSpec {
	input: GuardrailCheckSpec[];
	output: GuardrailCheckSpec[];
}

export interface AgentVersion {
	id: string;
	company_id: string;
	agent_id: string;
	version_no: number;
	spec: Record<string, unknown> & {
		identity?: AgentIdentity;
		model?: ModelSpec;
		tools?: { tool_id: string }[];
		skills?: string[];
		knowledge?: { kb_id: string }[];
		guardrails?: GuardrailsSpec;
	};
	spec_version: string;
	compiled_definition?: Record<string, unknown> | null;
	compiler_version?: string | null;
	dynamiq_version?: string | null;
	is_published: boolean;
	published_by?: string | null;
	published_at?: string | null;
}

export interface Deployment {
	id: string;
	company_id: string;
	slug: string;
	environment: 'staging' | 'production';
	agent_version_id: string;
	rate_limit_per_min?: number;
	daily_token_limit?: number;
	daily_cost_limit_usd?: number;
	allowed_origins?: string[];
	guardrail_overrides?: Record<string, unknown>;
	output_mode?: 'stream' | 'buffered';
	allow_write_tools?: boolean;
	conversation_ttl_days?: number;
	config_version?: number;
	enabled: boolean;
}

export interface ApiKey {
	id: string;
	company_id: string;
	name: string;
	prefix: string;
	allowed_ips?: string[];
	rate_limit_per_min?: number;
	daily_cost_limit_usd?: number;
	last_used_at?: string | null;
	created_by: string;
	revoked_at?: string | null;
}

export interface ApiKeyCreated extends ApiKey {
	key: string;
}

export type RunStatus = 'success' | 'error' | 'blocked' | 'interrupted' | 'truncated' | 'cancelled';
export type RunSource = 'playground' | 'api' | 'eval';

export interface Run {
	id: string;
	company_id: string;
	agent_version_id: string;
	deployment_id?: string | null;
	conversation_id?: string | null;
	source: RunSource;
	status: RunStatus;
	agent_name: string;
	model: string;
	tokens_in: number;
	tokens_out: number;
	cost_usd: number | null;
	latency_ms: number;
	trace_id: string;
	created_at: string;
}

export interface DashboardSummary {
	runs_today: number;
	tokens_this_month: { in: number; out: number };
	cost_this_month_usd: number;
	error_rate: number;
	guardrail_triggers: number;
}

// Phase 2/3/4 endpoints return a loosely-typed Placeholder body per the OpenAPI contract.
export type Placeholder = Record<string, unknown>;
