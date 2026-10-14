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

// CRUD + a standalone connectivity test + secret storage (PRD §6.5). `kind: http` and
// `kind: builtin` tools are wired into the agent spec/compiler for real.
export interface Tool {
	id: string;
	company_id: string;
	name: string;
	kind: 'http' | 'builtin' | 'python';
	access_level: 'read' | 'write';
	auth_mode: 'service' | 'delegated';
	audience?: string | null;
	// kind-specific: for http, url/method/headers/description/input_schema; for builtin,
	// { provider: 'tavily' | 'exa' } (always auth_mode: service).
	config: Record<string, unknown>;
	// Set only after PUT /tools/{id}/secret (auth_mode: service).
	masked_hint?: string | null;
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

// Evaluation datasets (PRD §6.7). No ai-runtime counterpart - pure console-api CRUD.
export interface Dataset {
	id: string;
	company_id: string;
	name: string;
	created_at: string;
	updated_at: string;
}

export interface DatasetInput {
	name?: string;
}

export interface DatasetItem {
	id: string;
	question: string;
	expected_answer: string | null;
	context: string | null;
	tags: string[];
	created_at: string;
}

export interface DatasetImportResult {
	inserted: number;
	errors: { row: number; error: string }[];
}

// Evaluation runs (PRD §6.7/§8.1). The single source of truth for the metrics checkbox
// list - llm_evaluator/python_evaluator aren't executable yet (no rubric schema, no
// sandboxing design - see ai/app/services/eval_runner.py's module docstring) and are
// rendered disabled rather than omitted, so the gap is visible instead of silent.
export const EVAL_METRICS = [
	{ name: 'faithfulness', label: 'Faithfulness', executable: true },
	{ name: 'context_recall', label: 'Context Recall', executable: true },
	{ name: 'context_precision', label: 'Context Precision', executable: true },
	{ name: 'answer_correctness', label: 'Answer Correctness', executable: true },
	{ name: 'factual_correctness', label: 'Factual Correctness', executable: true },
	{ name: 'bleu', label: 'BLEU', executable: true },
	{ name: 'rouge', label: 'ROUGE', executable: true },
	{ name: 'exact_match', label: 'Exact Match', executable: true },
	{ name: 'string_presence', label: 'String Presence', executable: true },
	{ name: 'string_similarity', label: 'String Similarity', executable: true },
	{ name: 'llm_evaluator', label: 'Custom Rubric (LLM Evaluator)', executable: false },
	{ name: 'python_evaluator', label: 'Python Evaluator', executable: false }
] as const;

export interface EvalCostEstimateInput {
	agent_version_id: string;
	dataset_id: string;
	metrics: string[];
	judge_model?: string;
}

export interface EvalCostEstimate {
	item_count: number;
	agent_model: string;
	judge_model: string;
	metrics: { metric: string; llm_calls_per_item: number }[];
	estimated_tokens: {
		agent_input: number;
		agent_output: number;
		judge_input: number;
		judge_output: number;
	};
	estimated_cost_usd: {
		agent: number | null;
		judge: number | null;
		total: number | null;
	};
	warnings: string[];
}

export interface CreateEvalRunInput {
	agent_version_id: string;
	dataset_id: string;
	metrics: string[];
	judge_model?: string | null;
	est_cost_usd?: number | null;
}

export interface EvalRunSummary {
	item_count: number;
	success_count: number;
	failure_count: number;
	mean_scores: Record<string, number>;
	total_cost_usd: number | null;
	skipped_metrics: string[];
	metric_errors: Record<string, string>;
}

export interface EvalRun {
	id: string;
	company_id: string;
	agent_version_id: string;
	dataset_id: string;
	role: string;
	metrics: string[];
	judge_model: string | null;
	status: 'queued' | 'running' | 'completed' | 'failed';
	est_cost_usd: number | null;
	summary: EvalRunSummary | null;
	error: string | null;
	created_at: string;
	updated_at: string;
}

export interface EvalResult {
	id: string;
	eval_run_id: string;
	dataset_item_id: string;
	run_id: string | null;
	scores: Record<string, number> | null;
	reasons: Record<string, string | null> | null;
	error: string | null;
	created_at: string;
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

// Mirrors ai/app/services/agent_spec.py's MemorySpec exactly (PRD §6.2).
export type MemorySaveMode = 'full' | 'input_output';

export interface MemorySpec {
	enabled: boolean;
	message_limit?: number | null;
	save_mode?: MemorySaveMode;
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
		memory?: MemorySpec;
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
