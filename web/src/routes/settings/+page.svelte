<script lang="ts">
	import { onMount } from 'svelte';
	import {
		listCompanyUsers,
		grantPlatformAdmin,
		revokePlatformAdmin,
		listEgressAllowlist,
		createEgressAllowlistEntry,
		deleteEgressAllowlistEntry,
		getAlertThresholds,
		updateAlertThresholds,
		getGuardrailPricing,
		updateGuardrailPricing,
		listCompanies,
		createCompany,
		suspendCompany,
		activateCompany,
		destroyCompanyDek,
		listTenants,
		createTenant,
		suspendTenant,
		activateTenant,
		listModelPricing,
		createModelPricing,
		updateModelPricing,
		deleteModelPricing,
		listAuditLogs
	} from '$lib/api/console';
	import { ApiError } from '$lib/api/client';
	import type {
		CompanyUser,
		Company,
		Tenant,
		EgressAllowlistEntry,
		ModelPricing,
		AuditLogEntry,
		AlertThresholds,
		GuardrailDetectorPricingEntry
	} from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { currentUser, roleForCompany } from '$lib/stores/auth';
	import { companyId } from '$lib/stores/company';

	// Egress allowlist is a per-company list (RLS-scoped, stamped with the caller's own
	// company_id on create) administered by that company's admins - requireAdminRole()
	// server-side, same as Connections, NOT platform_admin like the Companies/Pricing
	// tabs below.
	let isAdmin = $derived(roleForCompany($currentUser, $companyId) === 'admin');

	headerContent.set({ menu: 'Settings', title: 'Settings' });

	type TabId = 'users' | 'egress' | 'alerts' | 'guardrail-pricing' | 'tenants' | 'companies' | 'pricing' | 'audit';

	let activeTab = $state<TabId>('users');

	let users = $state<CompanyUser[]>([]);
	let usersLoading = $state(true);
	let usersError = $state<string | null>(null);
	let userActionId = $state<string | null>(null);
	let userActionError = $state<string | null>(null);

	let egress = $state<EgressAllowlistEntry[]>([]);
	let egressLoading = $state(true);
	let egressError = $state<string | null>(null);

	let showEgressForm = $state(false);
	// port is bound to a type="number" input, which Svelte coerces to number | null
	// (never a string, even for the initial value) - typing it as a number here avoids
	// calling string-only methods on it later.
	let egressForm = $state<{ host_pattern: string; port: number | null; allow_private_ip: boolean }>({
		host_pattern: '',
		port: null,
		allow_private_ip: false
	});
	let egressFormError = $state<string | null>(null);
	let submittingEgress = $state(false);
	let deletingEgressId = $state<string | null>(null);
	let egressDeleteError = $state<string | null>(null);

	const EGRESS_ERROR_MESSAGES: Record<string, string> = {
		host_pattern_required: 'Host pattern is required.',
		invalid_port: 'Port must be a whole number between 1 and 65535.',
		invalid_allow_private_ip: 'Allow private IP must be a yes/no value.',
		forbidden: 'You no longer have permission to do this.'
	};

	function describeEgressError(e: unknown, fallback: string): string {
		if (e instanceof ApiError) {
			return EGRESS_ERROR_MESSAGES[e.message] ?? e.message;
		}
		return e instanceof Error ? e.message : fallback;
	}

	// Dashboard's alert banner thresholds (console.alert_thresholds) - a single row per
	// company, not a list, so this is a plain load-edit-save form rather than the
	// egress/pricing tabs' add/delete-row pattern. `alertThresholdsForm` is a working
	// copy the inputs bind to - kept separate from `alertThresholds` (the last-saved
	// value) so "Reset" can discard unsaved edits without a re-fetch.
	let alertThresholds = $state<AlertThresholds | null>(null);
	let alertThresholdsForm = $state<AlertThresholds | null>(null);
	let alertThresholdsLoading = $state(true);
	let alertThresholdsError = $state<string | null>(null);
	let savingAlertThresholds = $state(false);
	let alertThresholdsSaveError = $state<string | null>(null);
	let alertThresholdsSaved = $state(false);

	async function loadAlertThresholds() {
		alertThresholdsLoading = true;
		alertThresholdsError = null;
		try {
			alertThresholds = await getAlertThresholds();
			alertThresholdsForm = { ...alertThresholds };
		} catch (e) {
			alertThresholdsError = e instanceof Error ? e.message : 'Failed to load alert thresholds.';
		} finally {
			alertThresholdsLoading = false;
		}
	}

	function resetAlertThresholdsForm() {
		if (alertThresholds) alertThresholdsForm = { ...alertThresholds };
		alertThresholdsSaveError = null;
	}

	async function saveAlertThresholds() {
		if (!alertThresholdsForm) return;
		savingAlertThresholds = true;
		alertThresholdsSaveError = null;
		alertThresholdsSaved = false;
		try {
			alertThresholds = await updateAlertThresholds(alertThresholdsForm);
			alertThresholdsForm = { ...alertThresholds };
			alertThresholdsSaved = true;
		} catch (e) {
			alertThresholdsSaveError =
				e instanceof ApiError && e.status === 403
					? 'You no longer have permission to do this.'
					: e instanceof Error
						? e.message
						: 'Failed to save alert thresholds.';
		} finally {
			savingAlertThresholds = false;
		}
	}

	// $/request pricing for the 3 Azure-backed guardrail detectors
	// (console.guardrail_detector_pricing) - feeds logs.runs.guardrail_cost_usd. Same
	// load-edit-save shape as the alert thresholds above, but the API returns/accepts a
	// list of 3 {check_type, price_per_request_usd} entries rather than one object - the
	// form keeps a flat Record<check_type, price> working copy instead, simpler to bind
	// each input to than indexing into an array by check_type on every keystroke.
	let guardrailPricing = $state<GuardrailDetectorPricingEntry[]>([]);
	let guardrailPricingForm = $state<Record<string, number>>({});
	let guardrailPricingLoading = $state(true);
	let guardrailPricingError = $state<string | null>(null);
	let savingGuardrailPricing = $state(false);
	let guardrailPricingSaveError = $state<string | null>(null);
	let guardrailPricingSaved = $state(false);

	const GUARDRAIL_CHECK_TYPE_LABELS: Record<string, string> = {
		prompt_injection: 'Prompt injection',
		pii_model: 'PII (model-based)',
		content_moderation: 'Content moderation'
	};

	function entriesToForm(entries: GuardrailDetectorPricingEntry[]): Record<string, number> {
		return Object.fromEntries(entries.map((e) => [e.check_type, e.price_per_request_usd]));
	}

	async function loadGuardrailPricing() {
		guardrailPricingLoading = true;
		guardrailPricingError = null;
		try {
			guardrailPricing = await getGuardrailPricing();
			guardrailPricingForm = entriesToForm(guardrailPricing);
		} catch (e) {
			guardrailPricingError = e instanceof Error ? e.message : 'Failed to load guardrail pricing.';
		} finally {
			guardrailPricingLoading = false;
		}
	}

	function resetGuardrailPricingForm() {
		guardrailPricingForm = entriesToForm(guardrailPricing);
		guardrailPricingSaveError = null;
	}

	async function saveGuardrailPricing() {
		savingGuardrailPricing = true;
		guardrailPricingSaveError = null;
		guardrailPricingSaved = false;
		try {
			guardrailPricing = await updateGuardrailPricing(guardrailPricingForm);
			guardrailPricingForm = entriesToForm(guardrailPricing);
			guardrailPricingSaved = true;
		} catch (e) {
			guardrailPricingSaveError =
				e instanceof ApiError && e.status === 403
					? 'You no longer have permission to do this.'
					: e instanceof Error
						? e.message
						: 'Failed to save guardrail pricing.';
		} finally {
			savingGuardrailPricing = false;
		}
	}

	let companies = $state<Company[]>([]);
	let companiesLoading = $state(true);
	let companiesError = $state<string | null>(null);

	let tenants = $state<Tenant[]>([]);
	let tenantsLoading = $state(true);
	let tenantsError = $state<string | null>(null);

	function tenantName(tenantId: string): string {
		return tenants.find((t) => t.id === tenantId)?.name ?? tenantId;
	}

	let pricing = $state<ModelPricing[]>([]);
	let pricingLoading = $state(true);
	let pricingError = $state<string | null>(null);
	let showPricingForm = $state(false);
	let editingPricingId = $state<string | null>(null);
	// input_per_1k/output_per_1k bind to type="number" inputs, which Svelte coerces to
	// number | null (same reasoning as egressForm.port/companyForm.monthly_budget_usd above).
	let pricingForm = $state<{
		provider: string;
		model: string;
		input_per_1k: number | null;
		output_per_1k: number | null;
	}>({ provider: '', model: '', input_per_1k: null, output_per_1k: null });
	let pricingFormError = $state<string | null>(null);
	let submittingPricing = $state(false);
	let pricingActionId = $state<string | null>(null);
	let pricingActionError = $state<string | null>(null);

	const PRICING_ERROR_MESSAGES: Record<string, string> = {
		provider_required: 'Provider is required.',
		model_required: 'Model is required.',
		invalid_input_per_1k: 'Input price must be a number.',
		invalid_output_per_1k: 'Output price must be a number.',
		provider_model_already_exists: 'A pricing row for this provider and model already exists.',
		forbidden: 'You no longer have permission to do this.',
		not_found: 'This pricing row no longer exists - it may have been changed by someone else.'
	};

	function describePricingError(e: unknown, fallback: string): string {
		if (e instanceof ApiError) return PRICING_ERROR_MESSAGES[e.message] ?? e.message;
		return e instanceof Error ? e.message : fallback;
	}

	async function loadPricing() {
		pricingLoading = true;
		pricingError = null;
		try {
			pricing = await listModelPricing();
		} catch (e) {
			pricingError = e instanceof Error ? e.message : 'Failed to load model pricing.';
		} finally {
			pricingLoading = false;
		}
	}

	function startCreatePricing() {
		editingPricingId = null;
		pricingForm = { provider: '', model: '', input_per_1k: null, output_per_1k: null };
		pricingFormError = null;
		showPricingForm = true;
	}

	function startEditPricing(row: ModelPricing) {
		editingPricingId = row.id;
		pricingForm = {
			provider: row.provider,
			model: row.model,
			input_per_1k: row.input_per_1k,
			output_per_1k: row.output_per_1k
		};
		pricingFormError = null;
		showPricingForm = true;
	}

	async function submitPricingForm() {
		submittingPricing = true;
		pricingFormError = null;
		try {
			const input = {
				provider: pricingForm.provider.trim(),
				model: pricingForm.model.trim(),
				input_per_1k: pricingForm.input_per_1k ?? 0,
				output_per_1k: pricingForm.output_per_1k ?? 0
			};
			if (editingPricingId === null) {
				await createModelPricing(input);
			} else {
				await updateModelPricing(editingPricingId, input);
			}
			showPricingForm = false;
			await loadPricing();
		} catch (e) {
			pricingFormError = describePricingError(e, 'Failed to save this pricing row.');
		} finally {
			submittingPricing = false;
		}
	}

	async function doDeletePricing(row: ModelPricing) {
		if (
			!confirm(
				`Delete pricing for "${row.provider} / ${row.model}"? Eval cost estimates for this model will no longer be available until it's added back.`
			)
		) {
			return;
		}
		pricingActionId = row.id;
		pricingActionError = null;
		try {
			await deleteModelPricing(row.id);
			await loadPricing();
		} catch (e) {
			pricingActionError = describePricingError(e, 'Failed to delete this pricing row.');
		} finally {
			pricingActionId = null;
		}
	}

	const AUDIT_LOG_PAGE_SIZE = 50;

	let auditLogs = $state<AuditLogEntry[]>([]);
	let auditLoading = $state(true);
	let auditError = $state<string | null>(null);
	let auditHasMore = $state(false);
	let auditOffset = $state(0);
	let auditFrom = $state('');
	let auditTo = $state('');
	let auditExpandedId = $state<string | null>(null);

	const AUDIT_LOG_ERROR_MESSAGES: Record<string, string> = {
		invalid_from: 'The "From" date is not valid.',
		invalid_to: 'The "To" date is not valid.',
		invalid_limit: 'The page size is not valid.',
		invalid_offset: 'The page offset is not valid.',
		forbidden: 'You no longer have permission to view audit logs.'
	};

	function describeAuditError(e: unknown, fallback: string): string {
		if (e instanceof ApiError) {
			return AUDIT_LOG_ERROR_MESSAGES[e.message] ?? e.message;
		}
		return e instanceof Error ? e.message : fallback;
	}

	async function loadAuditLogs(reset: boolean) {
		const offset = reset ? 0 : auditOffset;
		auditLoading = true;
		auditError = null;
		try {
			const response = await listAuditLogs({
				from: auditFrom || undefined,
				to: auditTo || undefined,
				limit: AUDIT_LOG_PAGE_SIZE,
				offset
			});
			auditLogs = reset ? response.items : [...auditLogs, ...response.items];
			auditHasMore = response.has_more;
			auditOffset = offset + response.items.length;
		} catch (e) {
			auditError = describeAuditError(e, 'Failed to load audit logs.');
		} finally {
			auditLoading = false;
		}
	}

	function clearAuditFilter() {
		auditFrom = '';
		auditTo = '';
		loadAuditLogs(true);
	}

	async function loadUsers() {
		usersLoading = true;
		usersError = null;
		try {
			users = await listCompanyUsers();
		} catch (e) {
			usersError = e instanceof Error ? e.message : 'Failed to load users.';
		} finally {
			usersLoading = false;
		}
	}

	const USER_ERROR_MESSAGES: Record<string, string> = {
		cannot_revoke_self: 'You cannot revoke your own platform admin access - ask another platform admin to do it.',
		not_found: 'This user no longer exists - it may have been changed by someone else.',
		forbidden: 'You no longer have permission to do this.'
	};

	function describeUserError(e: unknown, fallback: string): string {
		if (e instanceof ApiError) return USER_ERROR_MESSAGES[e.message] ?? e.message;
		return e instanceof Error ? e.message : fallback;
	}

	async function doGrantPlatformAdmin(user: CompanyUser) {
		userActionId = user.id;
		userActionError = null;
		try {
			await grantPlatformAdmin(user.id);
			await loadUsers();
		} catch (e) {
			userActionError = describeUserError(e, 'Failed to grant platform admin.');
		} finally {
			userActionId = null;
		}
	}

	async function doRevokePlatformAdmin(user: CompanyUser) {
		userActionId = user.id;
		userActionError = null;
		try {
			await revokePlatformAdmin(user.id);
			await loadUsers();
		} catch (e) {
			userActionError = describeUserError(e, 'Failed to revoke platform admin.');
		} finally {
			userActionId = null;
		}
	}

	async function loadEgress() {
		egressLoading = true;
		egressError = null;
		try {
			egress = await listEgressAllowlist();
		} catch (e) {
			egressError = e instanceof Error ? e.message : 'Failed to load the egress allowlist.';
		} finally {
			egressLoading = false;
		}
	}

	async function submitEgressForm() {
		submittingEgress = true;
		egressFormError = null;
		try {
			await createEgressAllowlistEntry({
				host_pattern: egressForm.host_pattern.trim(),
				port: egressForm.port ?? undefined,
				allow_private_ip: egressForm.allow_private_ip
			});
			showEgressForm = false;
			egressForm = { host_pattern: '', port: null, allow_private_ip: false };
			await loadEgress();
		} catch (e) {
			egressFormError = describeEgressError(e, 'Failed to add this host.');
		} finally {
			submittingEgress = false;
		}
	}

	async function doDeleteEgress(entry: EgressAllowlistEntry) {
		if (!confirm(`Remove "${entry.host_pattern}" from the egress allowlist?`)) return;
		deletingEgressId = entry.id;
		egressDeleteError = null;
		try {
			await deleteEgressAllowlistEntry(entry.id);
			await loadEgress();
		} catch (e) {
			// not_found means it's already gone (deleted elsewhere) - just reload quietly
			// instead of surfacing an alarming error for something that's already resolved.
			if (e instanceof ApiError && e.message === 'not_found') {
				await loadEgress();
			} else {
				egressDeleteError = describeEgressError(e, 'Failed to remove this host.');
			}
		} finally {
			deletingEgressId = null;
		}
	}

	async function loadCompanies() {
		companiesLoading = true;
		companiesError = null;
		try {
			companies = await listCompanies();
		} catch (e) {
			companiesError = e instanceof Error ? e.message : 'Failed to load companies.';
		} finally {
			companiesLoading = false;
		}
	}

	async function loadTenants() {
		tenantsLoading = true;
		tenantsError = null;
		try {
			tenants = await listTenants();
		} catch (e) {
			tenantsError = e instanceof Error ? e.message : 'Failed to load tenants.';
		} finally {
			tenantsLoading = false;
		}
	}

	const TENANT_ERROR_MESSAGES: Record<string, string> = {
		code_required: 'Code is required.',
		name_required: 'Name is required.',
		code_already_exists: 'A tenant with this code already exists.',
		forbidden: 'You no longer have permission to do this.',
		not_found: 'This tenant no longer exists - it may have been changed by someone else.'
	};

	function describeTenantError(e: unknown, fallback: string): string {
		if (e instanceof ApiError) return TENANT_ERROR_MESSAGES[e.message] ?? e.message;
		return e instanceof Error ? e.message : fallback;
	}

	let showTenantForm = $state(false);
	let tenantForm = $state({ code: '', name: '' });
	let tenantFormError = $state<string | null>(null);
	let submittingTenant = $state(false);
	let tenantActionId = $state<string | null>(null);
	let tenantActionError = $state<string | null>(null);

	async function submitTenantForm() {
		submittingTenant = true;
		tenantFormError = null;
		try {
			await createTenant({ code: tenantForm.code.trim(), name: tenantForm.name.trim() });
			showTenantForm = false;
			tenantForm = { code: '', name: '' };
			await loadTenants();
		} catch (e) {
			tenantFormError = describeTenantError(e, 'Failed to create this tenant.');
		} finally {
			submittingTenant = false;
		}
	}

	async function doSuspendTenant(tenant: Tenant) {
		tenantActionId = tenant.id;
		tenantActionError = null;
		try {
			await suspendTenant(tenant.id);
			await loadTenants();
		} catch (e) {
			tenantActionError = describeTenantError(e, 'Failed to suspend this tenant.');
		} finally {
			tenantActionId = null;
		}
	}

	async function doActivateTenant(tenant: Tenant) {
		tenantActionId = tenant.id;
		tenantActionError = null;
		try {
			await activateTenant(tenant.id);
			await loadTenants();
		} catch (e) {
			tenantActionError = describeTenantError(e, 'Failed to activate this tenant.');
		} finally {
			tenantActionId = null;
		}
	}

	const COMPANY_ERROR_MESSAGES: Record<string, string> = {
		tenant_id_required: 'Tenant is required.',
		tenant_not_found: 'The selected tenant no longer exists.',
		code_required: 'Code is required.',
		name_required: 'Name is required.',
		code_already_exists: 'A company with this code already exists.',
		invalid_monthly_budget_usd: 'Monthly budget must be a number.',
		forbidden: 'You no longer have permission to do this.',
		not_found: 'This company no longer exists - it may have been changed by someone else.'
	};

	function describeCompanyError(e: unknown, fallback: string): string {
		if (e instanceof ApiError) return COMPANY_ERROR_MESSAGES[e.message] ?? e.message;
		return e instanceof Error ? e.message : fallback;
	}

	let showCompanyForm = $state(false);
	// monthly_budget_usd is bound to a type="number" input, which Svelte coerces to
	// number | null (same reasoning as egressForm.port above).
	let companyForm = $state<{
		tenant_id: string;
		code: string;
		name: string;
		external_ref: string;
		monthly_budget_usd: number | null;
	}>({ tenant_id: '', code: '', name: '', external_ref: '', monthly_budget_usd: null });
	let companyFormError = $state<string | null>(null);
	let submittingCompany = $state(false);
	let companyActionId = $state<string | null>(null);
	let companyActionError = $state<string | null>(null);
	let destroyingDekId = $state<string | null>(null);
	let dekDestroyedIds = $state<Set<string>>(new Set());

	async function submitCompanyForm() {
		submittingCompany = true;
		companyFormError = null;
		try {
			await createCompany({
				tenant_id: companyForm.tenant_id,
				code: companyForm.code.trim(),
				name: companyForm.name.trim(),
				external_ref: companyForm.external_ref.trim() === '' ? undefined : companyForm.external_ref.trim(),
				monthly_budget_usd: companyForm.monthly_budget_usd ?? undefined
			});
			showCompanyForm = false;
			companyForm = { tenant_id: '', code: '', name: '', external_ref: '', monthly_budget_usd: null };
			await loadCompanies();
		} catch (e) {
			companyFormError = describeCompanyError(e, 'Failed to create this company.');
		} finally {
			submittingCompany = false;
		}
	}

	async function doSuspendCompany(company: Company) {
		companyActionId = company.id;
		companyActionError = null;
		try {
			await suspendCompany(company.id);
			await loadCompanies();
		} catch (e) {
			companyActionError = describeCompanyError(e, 'Failed to suspend this company.');
		} finally {
			companyActionId = null;
		}
	}

	async function doActivateCompany(company: Company) {
		companyActionId = company.id;
		companyActionError = null;
		try {
			await activateCompany(company.id);
			await loadCompanies();
		} catch (e) {
			companyActionError = describeCompanyError(e, 'Failed to activate this company.');
		} finally {
			companyActionId = null;
		}
	}

	async function doDestroyDek(company: Company) {
		if (
			!confirm(
				`Permanently destroy the encryption key for "${company.name}"? Every connection secret in this company becomes undecryptable immediately. This cannot be undone.`
			)
		) {
			return;
		}
		destroyingDekId = company.id;
		companyActionError = null;
		try {
			await destroyCompanyDek(company.id);
			dekDestroyedIds = new Set([...dekDestroyedIds, company.id]);
		} catch (e) {
			companyActionError = describeCompanyError(e, "Failed to destroy this company's encryption key.");
		} finally {
			destroyingDekId = null;
		}
	}

	onMount(() => {
		loadUsers();
		loadEgress();
	});

	// Same company_id-placeholder race every other list page handles (the initial fetch
	// can land before $companyId resolves to the real value) - re-fetch once it changes,
	// same pattern as datasets/+page.svelte's load().
	$effect(() => {
		$companyId;
		loadUsers();
		loadEgress();
	});

	$effect(() => {
		if (activeTab === 'alerts' && alertThresholds === null) {
			loadAlertThresholds();
		}
	});

	$effect(() => {
		if (activeTab === 'guardrail-pricing' && guardrailPricing.length === 0) {
			loadGuardrailPricing();
		}
	});

	$effect(() => {
		if (activeTab === 'companies' && $currentUser?.is_platform_admin && companies.length === 0) {
			loadCompanies();
		}
	});

	// Tenants are needed by both tabs - the Tenants tab itself, and the Companies tab's
	// create-form tenant picker + the tenant name shown per row.
	$effect(() => {
		if (
			(activeTab === 'tenants' || activeTab === 'companies') &&
			$currentUser?.is_platform_admin &&
			tenants.length === 0
		) {
			loadTenants();
		}
	});

	$effect(() => {
		if (activeTab === 'pricing' && $currentUser?.is_platform_admin && pricing.length === 0) {
			loadPricing();
		}
	});

	$effect(() => {
		if (activeTab === 'audit' && $currentUser?.is_platform_admin && auditLogs.length === 0) {
			loadAuditLogs(true);
		}
	});

	const roleDescriptions: Record<string, string> = {
		admin: 'Everything in the company, including Connections and code tools.',
		developer: 'Agents, HTTP Tools, KB, Eval, Playground.',
		viewer: 'Dashboard, Runs, Eval — read only.'
	};

	const tabs: { id: TabId; label: string; platformAdminOnly?: boolean }[] = [
		{ id: 'users', label: 'Users' },
		{ id: 'egress', label: 'Egress allowlist' },
		{ id: 'alerts', label: 'Alerts' },
		{ id: 'guardrail-pricing', label: 'Guardrail pricing' },
		{ id: 'tenants', label: 'Tenants', platformAdminOnly: true },
		{ id: 'companies', label: 'Companies', platformAdminOnly: true },
		{ id: 'pricing', label: 'Model pricing', platformAdminOnly: true },
		{ id: 'audit', label: 'Audit Log', platformAdminOnly: true }
	];
</script>

<div class="flex gap-1 border-b border-neutral-200">
	{#each tabs as tab (tab.id)}
		{@const disabled = tab.platformAdminOnly && !$currentUser?.is_platform_admin}
		<button
			type="button"
			class="px-4 py-2 text-sm {activeTab === tab.id
				? 'border-b-2 border-primary font-medium text-primary-700'
				: 'text-neutral-500 hover:text-neutral-800'} {disabled ? 'cursor-not-allowed opacity-40' : ''}"
			aria-current={activeTab === tab.id ? 'page' : undefined}
			aria-disabled={disabled}
			disabled={disabled}
			title={disabled ? 'platform_admin only' : undefined}
			onclick={() => (activeTab = tab.id)}
		>
			{tab.label}
		</button>
	{/each}
</div>

<div class="mt-4">
	{#if activeTab === 'users'}
		<div class="mb-4">
			<h2 class="text-sm font-semibold text-neutral-900">Users</h2>
			<p class="mt-1 text-sm text-neutral-400">
				Everyone synced into this company from SSO, and the role each one holds here.
			</p>
		</div>
		<div class="mb-4 grid grid-cols-1 gap-2 sm:grid-cols-3">
			{#each Object.entries(roleDescriptions) as [role, desc] (role)}
				<div class="rounded-md border border-neutral-200 bg-white p-3 text-xs">
					<p class="font-medium capitalize text-neutral-800">{role}</p>
					<p class="mt-1 text-neutral-500">{desc}</p>
				</div>
			{/each}
		</div>

		{#if userActionError}
			<div class="mb-4 rounded-md border border-danger/30 bg-danger/5 p-3">
				<p class="text-sm text-danger">{userActionError}</p>
			</div>
		{/if}

		{#if usersLoading}
			<p class="text-sm text-neutral-500" role="status">Loading users…</p>
		{:else if usersError}
			<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
				<p class="text-sm font-medium text-danger">Could not load users</p>
				<p class="mt-1 text-sm text-neutral-700">{usersError}</p>
				<button
					type="button"
					class="mt-3 rounded-md border border-danger/40 px-3 py-1.5 text-sm text-danger hover:bg-danger/10"
					onclick={loadUsers}
				>
					Retry
				</button>
			</div>
		{:else if users.length === 0}
			<div class="rounded-lg border border-dashed border-neutral-300 bg-white p-8 text-center">
				<p class="text-sm text-neutral-500">No users synced from SSO yet.</p>
			</div>
		{:else}
			<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
				<table class="min-w-full divide-y divide-neutral-200 text-sm">
					<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
						<tr>
							<th class="px-4 py-2">Email</th>
							<th class="px-4 py-2" title="This user's role within the current company.">Role</th>
							<th class="px-4 py-2" title="Platform admins can also manage Tenants, Companies and Audit Log.">
								Platform admin
							</th>
							{#if $currentUser?.is_platform_admin}
								<th class="px-4 py-2">Actions</th>
							{/if}
						</tr>
					</thead>
					<tbody class="divide-y divide-neutral-100">
						{#each users as user (user.id)}
							<tr>
								<td class="px-4 py-2 text-neutral-800">{user.email}</td>
								<td class="px-4 py-2 capitalize text-neutral-600">{user.role}</td>
								<td class="px-4 py-2">
									{#if user.is_platform_admin}
										<span class="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary-700">
											Yes
										</span>
									{:else}
										<span class="text-neutral-400">—</span>
									{/if}
								</td>
								{#if $currentUser?.is_platform_admin}
									<td class="px-4 py-2">
										{#if user.is_platform_admin}
											<button
												type="button"
												class="rounded-md border border-danger/40 px-2.5 py-1 text-xs text-danger hover:bg-danger/10 disabled:opacity-50"
												disabled={userActionId === user.id || user.id === $currentUser?.id}
												title={user.id === $currentUser?.id
													? 'Ask another platform admin to revoke your own access.'
													: undefined}
												onclick={() => doRevokePlatformAdmin(user)}
											>
												{userActionId === user.id ? 'Revoking…' : 'Revoke platform admin'}
											</button>
										{:else}
											<button
												type="button"
												class="rounded-md border border-neutral-300 px-2.5 py-1 text-xs text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
												disabled={userActionId === user.id}
												onclick={() => doGrantPlatformAdmin(user)}
											>
												{userActionId === user.id ? 'Granting…' : 'Grant platform admin'}
											</button>
										{/if}
									</td>
								{/if}
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	{:else if activeTab === 'egress'}
		<div class="mb-4 flex items-center justify-between gap-4">
			<div>
				<h2 class="text-sm font-semibold text-neutral-900">Egress allowlist</h2>
				<p class="mt-1 text-sm text-neutral-400">
					Connections can only reach hosts listed here. This protects internal services from
					SSRF (server-side request forgery) by blocking any outbound call to a host that isn't
					explicitly allowed — checked again on every save.
				</p>
			</div>
			{#if isAdmin}
				<button
					type="button"
					class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
					onclick={() => (showEgressForm = !showEgressForm)}
				>
					+ Add host
				</button>
			{:else}
				<span class="text-sm text-neutral-400">Admin access required to manage the egress allowlist</span>
			{/if}
		</div>

		{#if showEgressForm}
			<form
				class="mb-4 grid grid-cols-1 gap-4 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm sm:grid-cols-3"
				onsubmit={(e) => {
					e.preventDefault();
					submitEgressForm();
				}}
			>
				{#if egressFormError}
					<div class="sm:col-span-3 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
						{egressFormError}
					</div>
				{/if}
				<label class="block">
					<span class="text-sm font-medium text-neutral-700">Host pattern</span>
					<input
						type="text"
						bind:value={egressForm.host_pattern}
						required
						placeholder="api.openai.com"
						title="Exact hostname match, or a *.domain prefix to match its subdomains."
						class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 font-mono text-sm
							focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
					/>
					<p class="mt-1 text-xs text-neutral-400">
						Matched exactly (case-insensitive), e.g. <code>api.openai.com</code>. Use a
						<code>*.domain</code> prefix to match subdomains only — <code>*.example.com</code> matches
						<code>foo.example.com</code> but not <code>example.com</code> itself. No other wildcards
						are supported.
					</p>
				</label>
				<label class="block">
					<span class="text-sm font-medium text-neutral-700">Port (optional)</span>
					<input
						type="number"
						min="1"
						max="65535"
						bind:value={egressForm.port}
						placeholder="443"
						class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
							focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
					/>
				</label>
				<label class="flex items-end gap-2 pb-2">
					<input type="checkbox" bind:checked={egressForm.allow_private_ip} class="h-4 w-4" />
					<span class="text-sm text-neutral-700">Allow private IP</span>
				</label>
				<p class="text-xs text-neutral-400 sm:col-span-3">
					Only enable "Allow private IP" if this host genuinely resolves to an internal address you
					trust — it bypasses the usual SSRF protection for this entry.
				</p>
				<div class="flex gap-2 sm:col-span-3">
					<button
						type="submit"
						disabled={submittingEgress}
						class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
					>
						{submittingEgress ? 'Saving…' : 'Add host'}
					</button>
					<button
						type="button"
						class="rounded-md border border-neutral-300 px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
						onclick={() => (showEgressForm = false)}
					>
						Cancel
					</button>
				</div>
			</form>
		{/if}

		{#if egressDeleteError}
			<div class="mb-4 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
				{egressDeleteError}
			</div>
		{/if}

		{#if egressLoading}
			<p class="text-sm text-neutral-500" role="status">Loading egress allowlist…</p>
		{:else if egressError}
			<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
				<p class="text-sm font-medium text-danger">Could not load the egress allowlist</p>
				<p class="mt-1 text-sm text-neutral-700">{egressError}</p>
				<button
					type="button"
					class="mt-3 rounded-md border border-danger/40 px-3 py-1.5 text-sm text-danger hover:bg-danger/10"
					onclick={loadEgress}
				>
					Retry
				</button>
			</div>
		{:else if egress.length === 0}
			<div class="rounded-lg border border-dashed border-neutral-300 bg-white p-8 text-center">
				<p class="text-sm text-neutral-500">
					No egress allowlist entries yet — connections cannot reach any host until one is added.
				</p>
			</div>
		{:else}
			<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
				<table class="min-w-full divide-y divide-neutral-200 text-sm">
					<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
						<tr>
							<th class="px-4 py-2">Host pattern</th>
							<th class="px-4 py-2">Port</th>
							<th class="px-4 py-2">Allow private IP</th>
							<th class="px-4 py-2">Added</th>
							{#if isAdmin}
								<th class="px-4 py-2">Actions</th>
							{/if}
						</tr>
					</thead>
					<tbody class="divide-y divide-neutral-100">
						{#each egress as entry (entry.id)}
							<tr>
								<td class="px-4 py-2 font-mono text-xs text-neutral-800">{entry.host_pattern}</td>
								<td class="px-4 py-2 text-neutral-600">{entry.port ?? 'any'}</td>
								<td class="px-4 py-2 text-neutral-600">{entry.allow_private_ip ? 'Yes' : 'No'}</td>
								<td class="px-4 py-2 text-neutral-500">{new Date(entry.created_at).toLocaleString()}</td>
								{#if isAdmin}
									<td class="px-4 py-2">
										<button
											type="button"
											class="rounded-md border border-danger/40 px-2.5 py-1 text-xs text-danger hover:bg-danger/10 disabled:opacity-50"
											disabled={deletingEgressId === entry.id}
											onclick={() => doDeleteEgress(entry)}
										>
											{deletingEgressId === entry.id ? 'Removing…' : 'Delete'}
										</button>
									</td>
								{/if}
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	{:else if activeTab === 'alerts'}
		<div class="mb-4">
			<h2 class="text-sm font-semibold text-neutral-900">Dashboard alert thresholds</h2>
			<p class="mt-1 text-sm text-neutral-400">
				Controls the alert banner on the Dashboard page — each alert compares today's
				activity against this company's own recent daily average, not a fixed number, so
				these mostly need adjusting only if the banner fires too often or not often enough
				for how this company actually runs.
			</p>
		</div>

		{#if alertThresholdsLoading}
			<p class="text-sm text-neutral-500" role="status">Loading alert thresholds…</p>
		{:else if alertThresholdsError}
			<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
				<p class="text-sm font-medium text-danger">Could not load alert thresholds</p>
				<p class="mt-1 text-sm text-neutral-700">{alertThresholdsError}</p>
				<button
					type="button"
					class="mt-3 rounded-md border border-danger/40 px-3 py-1.5 text-sm text-danger hover:bg-danger/10"
					onclick={loadAlertThresholds}
				>
					Retry
				</button>
			</div>
		{:else if alertThresholdsForm}
			{#if !isAdmin}
				<p class="mb-4 text-sm text-neutral-400">Admin access required to change these.</p>
			{/if}
			<form
				class="max-w-2xl space-y-6 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm"
				onsubmit={(e) => {
					e.preventDefault();
					saveAlertThresholds();
				}}
			>
				{#if alertThresholdsSaveError}
					<div class="rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
						{alertThresholdsSaveError}
					</div>
				{/if}
				{#if alertThresholdsSaved}
					<div class="rounded-md border border-primary/30 bg-primary/5 p-3 text-sm text-primary-700">
						Saved.
					</div>
				{/if}

				<fieldset disabled={!isAdmin} class="space-y-6">
					<div>
						<h3 class="text-sm font-medium text-neutral-800">Quota</h3>
						<p class="mt-1 text-xs text-neutral-400">
							Fires once this company has spent this share of its monthly budget (Settings has no
							budget field itself yet — set via the Companies tab's own budget, platform_admin
							only). No budget set means this alert never fires, regardless of this value.
						</p>
						<label class="mt-2 block max-w-xs">
							<span class="text-sm text-neutral-700">Budget used (%)</span>
							<input
								type="number"
								min="0"
								max="100"
								step="1"
								bind:value={alertThresholdsForm.quota_pct}
								class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
									focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
							/>
						</label>
					</div>

					<div>
						<h3 class="text-sm font-medium text-neutral-800">Error rate</h3>
						<p class="mt-1 text-xs text-neutral-400">
							Fires when today's error rate is at least this many times the usual rate, AND today
							had at least this many errors, AND today's error rate is at least this percentage —
							all three, so a single error on a quiet day doesn't trigger it.
						</p>
						<div class="mt-2 grid grid-cols-1 gap-4 sm:grid-cols-3">
							<label class="block">
								<span class="text-sm text-neutral-700">Multiple of usual (×)</span>
								<input
									type="number"
									min="1"
									step="0.1"
									bind:value={alertThresholdsForm.error_rate_multiplier}
									class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
										focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
								/>
							</label>
							<label class="block">
								<span class="text-sm text-neutral-700">Min. errors today</span>
								<input
									type="number"
									min="0"
									step="1"
									bind:value={alertThresholdsForm.error_rate_min_errors}
									class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
										focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
								/>
							</label>
							<label class="block">
								<span class="text-sm text-neutral-700">Min. error rate (%)</span>
								<input
									type="number"
									min="0"
									max="100"
									step="1"
									bind:value={alertThresholdsForm.error_rate_min_pct}
									class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
										focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
								/>
							</label>
						</div>
					</div>

					<div>
						<h3 class="text-sm font-medium text-neutral-800">Guardrail triggers</h3>
						<p class="mt-1 text-xs text-neutral-400">
							Fires when today's guardrail checks (block + mask + flag combined) are at least this
							many times the usual daily count, AND at least this many happened today.
						</p>
						<div class="mt-2 grid grid-cols-1 gap-4 sm:grid-cols-2">
							<label class="block">
								<span class="text-sm text-neutral-700">Multiple of usual (×)</span>
								<input
									type="number"
									min="1"
									step="0.1"
									bind:value={alertThresholdsForm.guardrail_multiplier}
									class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
										focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
								/>
							</label>
							<label class="block">
								<span class="text-sm text-neutral-700">Min. triggers today</span>
								<input
									type="number"
									min="0"
									step="1"
									bind:value={alertThresholdsForm.guardrail_min_count}
									class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
										focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
								/>
							</label>
						</div>
					</div>

					<div>
						<h3 class="text-sm font-medium text-neutral-800">Latency</h3>
						<p class="mt-1 text-xs text-neutral-400">
							Fires when today's p95 latency is at least this many times the usual p95, AND at
							least this many milliseconds — the floor keeps a jump between two already-fast times
							(e.g. 50ms → 110ms) from counting as a spike.
						</p>
						<div class="mt-2 grid grid-cols-1 gap-4 sm:grid-cols-2">
							<label class="block">
								<span class="text-sm text-neutral-700">Multiple of usual (×)</span>
								<input
									type="number"
									min="1"
									step="0.1"
									bind:value={alertThresholdsForm.latency_multiplier}
									class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
										focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
								/>
							</label>
							<label class="block">
								<span class="text-sm text-neutral-700">Min. p95 latency (ms)</span>
								<input
									type="number"
									min="0"
									step="100"
									bind:value={alertThresholdsForm.latency_min_ms}
									class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
										focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
								/>
							</label>
						</div>
					</div>

					<div>
						<h3 class="text-sm font-medium text-neutral-800">Cost</h3>
						<p class="mt-1 text-xs text-neutral-400">
							Fires when today's cost is at least this many times the usual daily cost, AND at
							least this many dollars — the floor keeps a jump between two tiny amounts from
							counting as a spike.
						</p>
						<div class="mt-2 grid grid-cols-1 gap-4 sm:grid-cols-2">
							<label class="block">
								<span class="text-sm text-neutral-700">Multiple of usual (×)</span>
								<input
									type="number"
									min="1"
									step="0.1"
									bind:value={alertThresholdsForm.cost_multiplier}
									class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
										focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
								/>
							</label>
							<label class="block">
								<span class="text-sm text-neutral-700">Min. cost today ($)</span>
								<input
									type="number"
									min="0"
									step="0.01"
									bind:value={alertThresholdsForm.cost_min_usd}
									class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
										focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
								/>
							</label>
						</div>
					</div>
				</fieldset>

				{#if isAdmin}
					<div class="flex gap-2 border-t border-neutral-100 pt-4">
						<button
							type="submit"
							disabled={savingAlertThresholds}
							class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
						>
							{savingAlertThresholds ? 'Saving…' : 'Save'}
						</button>
						<button
							type="button"
							class="rounded-md border border-neutral-300 px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
							onclick={resetAlertThresholdsForm}
						>
							Reset
						</button>
					</div>
				{/if}
			</form>
		{/if}
	{:else if activeTab === 'guardrail-pricing'}
		<div class="mb-4">
			<h2 class="text-sm font-semibold text-neutral-900">Guardrail detector pricing</h2>
			<p class="mt-1 text-sm text-neutral-400">
				Azure bills the prompt injection, PII (model-based), and content moderation
				guardrail checks per request, not per token — there's no automatic price the way
				there is for LLM calls. Set your own Azure subscription's real rate here so Run
				detail/Dashboard cost figures reflect it; a check left at $0 is simply never
				charged for, not estimated.
			</p>
		</div>

		{#if guardrailPricingLoading}
			<p class="text-sm text-neutral-500" role="status">Loading guardrail pricing…</p>
		{:else if guardrailPricingError}
			<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
				<p class="text-sm font-medium text-danger">Could not load guardrail pricing</p>
				<p class="mt-1 text-sm text-neutral-700">{guardrailPricingError}</p>
				<button
					type="button"
					class="mt-3 rounded-md border border-danger/40 px-3 py-1.5 text-sm text-danger hover:bg-danger/10"
					onclick={loadGuardrailPricing}
				>
					Retry
				</button>
			</div>
		{:else}
			{#if !isAdmin}
				<p class="mb-4 text-sm text-neutral-400">Admin access required to change these.</p>
			{/if}
			<form
				class="max-w-md space-y-4 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm"
				onsubmit={(e) => {
					e.preventDefault();
					saveGuardrailPricing();
				}}
			>
				{#if guardrailPricingSaveError}
					<div class="rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
						{guardrailPricingSaveError}
					</div>
				{/if}
				{#if guardrailPricingSaved}
					<div class="rounded-md border border-primary/30 bg-primary/5 p-3 text-sm text-primary-700">
						Saved.
					</div>
				{/if}

				<fieldset disabled={!isAdmin} class="space-y-4">
					{#each guardrailPricing as entry (entry.check_type)}
						<label class="block">
							<span class="text-sm text-neutral-700">
								{GUARDRAIL_CHECK_TYPE_LABELS[entry.check_type] ?? entry.check_type}
							</span>
							<div class="mt-1 flex items-center gap-2">
								<span class="text-sm text-neutral-400">$</span>
								<input
									type="number"
									min="0"
									step="0.0001"
									bind:value={guardrailPricingForm[entry.check_type]}
									class="w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
										focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
								/>
								<span class="shrink-0 text-xs text-neutral-400">/ request</span>
							</div>
						</label>
					{/each}
				</fieldset>

				{#if isAdmin}
					<div class="flex gap-2 border-t border-neutral-100 pt-4">
						<button
							type="submit"
							disabled={savingGuardrailPricing}
							class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
						>
							{savingGuardrailPricing ? 'Saving…' : 'Save'}
						</button>
						<button
							type="button"
							class="rounded-md border border-neutral-300 px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
							onclick={resetGuardrailPricingForm}
						>
							Reset
						</button>
					</div>
				{/if}
			</form>
		{/if}
	{:else if activeTab === 'tenants'}
		{#if !$currentUser?.is_platform_admin}
			<div class="rounded-md border border-neutral-200 bg-white p-8 text-center text-sm text-neutral-500">
				platform_admin only.
			</div>
		{:else}
			<div class="mb-4 flex items-center justify-between gap-4">
				<div>
					<h2 class="text-sm font-semibold text-neutral-900">Tenants</h2>
					<p class="mt-1 text-sm text-neutral-400">
						A Tenant is the top-level organization grouping in the console. Each Tenant holds one
						or more Companies underneath it — the billing and budget units where API keys,
						connections and usage actually live. Most deployments only ever need a single Tenant.
					</p>
				</div>
				<button
					type="button"
					class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
					onclick={() => (showTenantForm = !showTenantForm)}
				>
					+ New tenant
				</button>
			</div>

			{#if showTenantForm}
				<form
					class="mb-4 grid grid-cols-1 gap-4 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm sm:grid-cols-2"
					onsubmit={(e) => {
						e.preventDefault();
						submitTenantForm();
					}}
				>
					{#if tenantFormError}
						<div class="sm:col-span-2 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
							{tenantFormError}
						</div>
					{/if}
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Code</span>
						<input
							type="text"
							bind:value={tenantForm.code}
							required
							placeholder="advws"
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 font-mono text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						/>
						<p class="mt-1 text-xs text-neutral-400">
							Short, unique identifier shown throughout the console. Cannot be changed after
							creation - there's no edit for tenants once created.
						</p>
					</label>
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Name</span>
						<input
							type="text"
							bind:value={tenantForm.name}
							required
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						/>
					</label>
					<div class="flex gap-2 sm:col-span-2">
						<button
							type="submit"
							disabled={submittingTenant}
							class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
						>
							{submittingTenant ? 'Saving…' : 'Save tenant'}
						</button>
						<button
							type="button"
							class="rounded-md border border-neutral-300 px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
							onclick={() => (showTenantForm = false)}
						>
							Cancel
						</button>
					</div>
				</form>
			{/if}

			{#if tenantActionError}
				<div class="mb-4 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
					{tenantActionError}
				</div>
			{/if}

			{#if tenantsLoading}
				<p class="text-sm text-neutral-500" role="status">Loading tenants…</p>
			{:else if tenantsError}
				<div class="rounded-md border border-danger/30 bg-danger/5 p-4 text-sm text-danger">{tenantsError}</div>
			{:else if tenants.length === 0}
				<div class="rounded-lg border border-dashed border-neutral-300 bg-white p-8 text-center">
					<p class="text-sm text-neutral-500">No tenants yet.</p>
				</div>
			{:else}
				<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
					<table class="min-w-full divide-y divide-neutral-200 text-sm">
						<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
							<tr>
								<th class="px-4 py-2">Code</th>
								<th class="px-4 py-2">Name</th>
								<th class="px-4 py-2">Status</th>
								<th class="px-4 py-2">Actions</th>
							</tr>
						</thead>
						<tbody class="divide-y divide-neutral-100">
							{#each tenants as tenant (tenant.id)}
								<tr>
									<td class="px-4 py-2 font-mono text-xs">{tenant.code}</td>
									<td class="px-4 py-2 text-neutral-800">{tenant.name}</td>
									<td class="px-4 py-2">
										<span
											class="rounded-full px-2 py-0.5 text-xs font-medium
												{tenant.status === 'active' ? 'bg-primary/10 text-primary-700' : 'bg-danger/10 text-danger'}"
										>
											{tenant.status}
										</span>
									</td>
									<td class="px-4 py-2">
										{#if tenant.status === 'active'}
											<button
												type="button"
												class="rounded-md border border-danger/40 px-2.5 py-1 text-xs text-danger hover:bg-danger/10 disabled:opacity-50"
												disabled={tenantActionId === tenant.id}
												onclick={() => doSuspendTenant(tenant)}
											>
												{tenantActionId === tenant.id ? 'Suspending…' : 'Suspend'}
											</button>
										{:else}
											<button
												type="button"
												class="rounded-md border border-neutral-300 px-2.5 py-1 text-xs text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
												disabled={tenantActionId === tenant.id}
												onclick={() => doActivateTenant(tenant)}
											>
												{tenantActionId === tenant.id ? 'Activating…' : 'Activate'}
											</button>
										{/if}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		{/if}
	{:else if activeTab === 'companies'}
		{#if !$currentUser?.is_platform_admin}
			<div class="rounded-md border border-neutral-200 bg-white p-8 text-center text-sm text-neutral-500">
				platform_admin only.
			</div>
		{:else}
			<div class="mb-4 flex items-center justify-between gap-4">
				<div>
					<h2 class="text-sm font-semibold text-neutral-900">Companies</h2>
					<p class="mt-1 text-sm text-neutral-400">
						A Company is the billing and budget unit that lives under a Tenant — API keys,
						connections, usage and budgets are all scoped to it. platform_admin sees only
						aggregate figures here — never secrets, knowledge or conversation content.
					</p>
				</div>
				{#if tenants.length > 0}
					<button
						type="button"
						class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
						onclick={() => {
							if (!showCompanyForm) companyForm.tenant_id = tenants[0]?.id ?? '';
							showCompanyForm = !showCompanyForm;
						}}
					>
						+ New company
					</button>
				{:else}
					<span class="text-sm text-neutral-400">Create a tenant first, under the Tenants tab</span>
				{/if}
			</div>

			{#if showCompanyForm}
				<form
					class="mb-4 grid grid-cols-1 gap-4 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm sm:grid-cols-2"
					onsubmit={(e) => {
						e.preventDefault();
						submitCompanyForm();
					}}
				>
					{#if companyFormError}
						<div class="sm:col-span-2 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
							{companyFormError}
						</div>
					{/if}
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Tenant</span>
						<select
							bind:value={companyForm.tenant_id}
							required
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						>
							{#each tenants as tenant (tenant.id)}
								<option value={tenant.id}>{tenant.name}</option>
							{/each}
						</select>
					</label>
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Code</span>
						<input
							type="text"
							bind:value={companyForm.code}
							required
							placeholder="operations"
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 font-mono text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						/>
						<p class="mt-1 text-xs text-neutral-400">
							Unique within the tenant - embedded into every API key prefix generated for this
							company (<code>ak_&lcub;code&rcub;_...</code>). No edit after creation.
						</p>
					</label>
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Name</span>
						<input
							type="text"
							bind:value={companyForm.name}
							required
							placeholder="Operations"
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						/>
					</label>
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">External ref (optional)</span>
						<input
							type="text"
							bind:value={companyForm.external_ref}
							placeholder="matches the SSO JWT's companies[].ref claim"
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						/>
						<span class="mt-1 block text-xs text-neutral-400">
							Users only get synced into this company on login if their SSO claim's ref matches this.
						</span>
					</label>
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Monthly budget USD (optional)</span>
						<input
							type="number"
							min="0"
							step="0.01"
							bind:value={companyForm.monthly_budget_usd}
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						/>
					</label>
					<div class="flex gap-2 sm:col-span-2">
						<button
							type="submit"
							disabled={submittingCompany}
							class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
						>
							{submittingCompany ? 'Saving…' : 'Save company'}
						</button>
						<button
							type="button"
							class="rounded-md border border-neutral-300 px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
							onclick={() => (showCompanyForm = false)}
						>
							Cancel
						</button>
					</div>
				</form>
			{/if}

			{#if companyActionError}
				<div class="mb-4 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
					{companyActionError}
				</div>
			{/if}

			{#if companiesLoading}
				<p class="text-sm text-neutral-500" role="status">Loading companies…</p>
			{:else if companiesError}
				<div class="rounded-md border border-danger/30 bg-danger/5 p-4 text-sm text-danger">{companiesError}</div>
			{:else if companies.length === 0}
				<div class="rounded-lg border border-dashed border-neutral-300 bg-white p-8 text-center">
					<p class="text-sm text-neutral-500">No companies yet.</p>
				</div>
			{:else}
				<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
					<table class="min-w-full divide-y divide-neutral-200 text-sm">
						<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
							<tr>
								<th class="px-4 py-2">Code</th>
								<th class="px-4 py-2">Name</th>
								<th class="px-4 py-2">Tenant</th>
								<th class="px-4 py-2">Status</th>
								<th class="px-4 py-2">Monthly budget</th>
								<th class="px-4 py-2">Actions</th>
							</tr>
						</thead>
						<tbody class="divide-y divide-neutral-100">
							{#each companies as company (company.id)}
								<tr>
									<td class="px-4 py-2 font-mono text-xs">{company.code}</td>
									<td class="px-4 py-2 text-neutral-800">{company.name}</td>
									<td class="px-4 py-2 text-neutral-600">{tenantName(company.tenant_id)}</td>
									<td class="px-4 py-2">
										<span
											class="rounded-full px-2 py-0.5 text-xs font-medium
												{company.status === 'active' ? 'bg-primary/10 text-primary-700' : 'bg-danger/10 text-danger'}"
										>
											{company.status}
										</span>
									</td>
									<td class="px-4 py-2 text-neutral-600">
										{company.monthly_budget_usd != null ? `$${company.monthly_budget_usd}` : '—'}
									</td>
									<td class="px-4 py-2">
										<div class="flex flex-wrap items-center gap-2">
											{#if company.status === 'active'}
												<button
													type="button"
													class="rounded-md border border-neutral-300 px-2.5 py-1 text-xs text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
													disabled={companyActionId === company.id}
													onclick={() => doSuspendCompany(company)}
												>
													{companyActionId === company.id ? 'Suspending…' : 'Suspend'}
												</button>
											{:else}
												<button
													type="button"
													class="rounded-md border border-neutral-300 px-2.5 py-1 text-xs text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
													disabled={companyActionId === company.id}
													onclick={() => doActivateCompany(company)}
												>
													{companyActionId === company.id ? 'Activating…' : 'Activate'}
												</button>
											{/if}
											{#if dekDestroyedIds.has(company.id)}
												<span class="text-xs text-danger">Key destroyed</span>
											{:else}
												<div class="flex flex-col items-start gap-1">
													<button
														type="button"
														class="rounded-md border border-danger/40 px-2.5 py-1 text-xs text-danger hover:bg-danger/10 disabled:opacity-50"
														disabled={destroyingDekId === company.id}
														onclick={() => doDestroyDek(company)}
													>
														{destroyingDekId === company.id ? 'Destroying…' : 'Destroy key'}
													</button>
													<span class="text-[11px] leading-tight text-danger/80">
														Irreversible — permanently undecryptable connection secrets
													</span>
												</div>
											{/if}
										</div>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		{/if}
	{:else if activeTab === 'pricing'}
		{#if !$currentUser?.is_platform_admin}
			<div class="rounded-md border border-neutral-200 bg-white p-8 text-center text-sm text-neutral-500">
				platform_admin only.
			</div>
		{:else}
			<div class="mb-4 flex items-center justify-between gap-4">
				<div>
					<h2 class="text-sm font-semibold text-neutral-900">Model pricing</h2>
					<p class="mt-1 text-sm text-neutral-400">
						$/1K-token rates used to estimate the cost of Eval runs.
					</p>
				</div>
				<button
					type="button"
					class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
					onclick={startCreatePricing}
				>
					+ New pricing row
				</button>
			</div>

			<div class="mb-4 rounded-md border border-warning/40 bg-warning/10 p-4">
				<p class="text-sm font-medium text-warning-dark">These prices only affect Eval cost estimates</p>
				<p class="mt-1 text-sm text-neutral-700">
					Editing a row here changes the estimated cost shown on Eval runs only. Real spend is
					billed by litellm directly from each provider's actual rates — it never reads this
					table, so these numbers can drift from what you're actually charged if not kept in sync.
				</p>
			</div>

			{#if showPricingForm}
				<form
					class="mb-4 grid grid-cols-1 gap-4 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm sm:grid-cols-2"
					onsubmit={(e) => {
						e.preventDefault();
						submitPricingForm();
					}}
				>
					{#if pricingFormError}
						<div class="sm:col-span-2 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
							{pricingFormError}
						</div>
					{/if}
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Provider</span>
						<input
							type="text"
							bind:value={pricingForm.provider}
							required
							placeholder="anthropic"
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						/>
					</label>
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Model</span>
						<input
							type="text"
							bind:value={pricingForm.model}
							required
							placeholder="claude-sonnet-5"
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 font-mono text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						/>
					</label>
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Input $ per 1K tokens</span>
						<input
							type="number"
							min="0"
							step="0.000001"
							bind:value={pricingForm.input_per_1k}
							required
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						/>
					</label>
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Output $ per 1K tokens</span>
						<input
							type="number"
							min="0"
							step="0.000001"
							bind:value={pricingForm.output_per_1k}
							required
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						/>
					</label>
					<div class="flex gap-2 sm:col-span-2">
						<button
							type="submit"
							disabled={submittingPricing}
							class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
						>
							{submittingPricing ? 'Saving…' : 'Save pricing row'}
						</button>
						<button
							type="button"
							class="rounded-md border border-neutral-300 px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
							onclick={() => (showPricingForm = false)}
						>
							Cancel
						</button>
					</div>
				</form>
			{/if}

			{#if pricingActionError}
				<div class="mb-4 rounded-md border border-danger/30 bg-danger/5 p-3">
					<p class="text-sm text-danger">{pricingActionError}</p>
				</div>
			{/if}

			{#if pricingLoading}
				<p class="text-sm text-neutral-500" role="status">Loading model pricing…</p>
			{:else if pricingError}
				<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
					<p class="text-sm font-medium text-danger">Could not load model pricing</p>
					<p class="mt-1 text-sm text-neutral-700">{pricingError}</p>
					<button
						type="button"
						class="mt-3 rounded-md border border-danger/40 px-3 py-1.5 text-sm text-danger hover:bg-danger/10"
						onclick={loadPricing}
					>
						Retry
					</button>
				</div>
			{:else if pricing.length === 0}
				<div class="rounded-lg border border-dashed border-neutral-300 bg-white p-8 text-center">
					<p class="text-sm text-neutral-500">No pricing rows yet.</p>
				</div>
			{:else}
				<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
					<table class="min-w-full divide-y divide-neutral-200 text-sm">
						<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
							<tr>
								<th class="px-4 py-2">Provider</th>
								<th class="px-4 py-2">Model</th>
								<th class="px-4 py-2">Input $/1K</th>
								<th class="px-4 py-2">Output $/1K</th>
								<th class="px-4 py-2">Actions</th>
							</tr>
						</thead>
						<tbody class="divide-y divide-neutral-100">
							{#each pricing as row (row.id)}
								<tr>
									<td class="px-4 py-2 text-neutral-800">{row.provider}</td>
									<td class="px-4 py-2 font-mono text-xs text-neutral-800">{row.model}</td>
									<td class="px-4 py-2 text-neutral-600">{row.input_per_1k}</td>
									<td class="px-4 py-2 text-neutral-600">{row.output_per_1k}</td>
									<td class="px-4 py-2">
										<div class="flex gap-2">
											<button
												type="button"
												class="rounded-md border border-neutral-300 px-2.5 py-1 text-xs text-neutral-700 hover:bg-neutral-50"
												onclick={() => startEditPricing(row)}
											>
												Edit
											</button>
											<button
												type="button"
												class="rounded-md border border-danger/40 px-2.5 py-1 text-xs text-danger hover:bg-danger/10 disabled:opacity-50"
												disabled={pricingActionId === row.id}
												onclick={() => doDeletePricing(row)}
											>
												{pricingActionId === row.id ? 'Deleting…' : 'Delete'}
											</button>
										</div>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		{/if}
	{:else if activeTab === 'audit'}
		{#if !$currentUser?.is_platform_admin}
			<div class="rounded-md border border-neutral-200 bg-white p-8 text-center text-sm text-neutral-500">
				platform_admin only.
			</div>
		{:else}
			<div class="mb-4">
				<h2 class="text-sm font-semibold text-neutral-900">Audit Log</h2>
				<p class="mt-1 text-sm text-neutral-400">
					A record of admin actions taken in this console — who changed what, and when.
				</p>
			</div>
			<div class="mb-4 flex flex-wrap items-end gap-3">
				<label class="block">
					<span class="text-sm font-medium text-neutral-700">From</span>
					<input
						type="date"
						bind:value={auditFrom}
						class="mt-1 rounded-md border border-neutral-300 px-3 py-2 text-sm
							focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
					/>
				</label>
				<label class="block">
					<span class="text-sm font-medium text-neutral-700">To</span>
					<input
						type="date"
						bind:value={auditTo}
						class="mt-1 rounded-md border border-neutral-300 px-3 py-2 text-sm
							focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
					/>
				</label>
				<button
					type="button"
					class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
					onclick={() => loadAuditLogs(true)}
				>
					Apply
				</button>
				<button
					type="button"
					class="rounded-md border border-neutral-300 px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
					onclick={clearAuditFilter}
				>
					Clear
				</button>
			</div>

			{#if auditError}
				<div class="mb-4 rounded-md border border-danger/30 bg-danger/5 p-4">
					<p class="text-sm font-medium text-danger">Could not load audit logs</p>
					<p class="mt-1 text-sm text-neutral-700">{auditError}</p>
					<button
						type="button"
						class="mt-3 rounded-md border border-danger/40 px-3 py-1.5 text-sm text-danger hover:bg-danger/10"
						onclick={() => loadAuditLogs(true)}
					>
						Retry
					</button>
				</div>
			{/if}

			{#if auditLoading && auditLogs.length === 0}
				<p class="text-sm text-neutral-500" role="status">Loading audit logs…</p>
			{:else if !auditError && auditLogs.length === 0}
				<div class="rounded-lg border border-dashed border-neutral-300 bg-white p-8 text-center">
					<p class="text-sm text-neutral-500">No audit log entries for this filter.</p>
				</div>
			{:else if auditLogs.length > 0}
				<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
					<table class="min-w-full divide-y divide-neutral-200 text-sm">
						<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
							<tr>
								<th class="px-4 py-2">Time</th>
								<th class="px-4 py-2">Company</th>
								<th class="px-4 py-2">User</th>
								<th class="px-4 py-2">Action</th>
								<th class="px-4 py-2">Entity</th>
								<th class="px-4 py-2" title="The row this action affected, when applicable.">Entity ID</th>
								<th class="px-4 py-2"></th>
							</tr>
						</thead>
						<tbody class="divide-y divide-neutral-100">
							{#each auditLogs as entry (entry.id)}
								<tr>
									<td class="px-4 py-2 text-neutral-600">{new Date(entry.created_at).toLocaleString()}</td>
									<td class="px-4 py-2 text-neutral-600">
										{#if entry.company_name}
											<span class="rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-500">
												{entry.company_name}
											</span>
										{:else}
											<span class="text-xs text-neutral-400">Platform</span>
										{/if}
									</td>
									<td class="px-4 py-2 text-neutral-600">{entry.user_email ?? 'system'}</td>
									<td class="px-4 py-2 font-mono text-xs text-neutral-600">{entry.action}</td>
									<td class="px-4 py-2 text-neutral-600">{entry.entity}</td>
									<td class="px-4 py-2 font-mono text-xs text-neutral-500">{entry.entity_id ?? '—'}</td>
									<td class="px-4 py-2 text-right">
										{#if entry.diff}
											<button
												type="button"
												class="text-xs text-primary underline underline-offset-2"
												onclick={() => (auditExpandedId = auditExpandedId === entry.id ? null : entry.id)}
											>
												{auditExpandedId === entry.id ? 'Hide diff' : 'View diff'}
											</button>
										{/if}
									</td>
								</tr>
								{#if auditExpandedId === entry.id && entry.diff}
									<tr>
										<td colspan="7" class="bg-neutral-50 px-4 py-2">
											<pre class="overflow-x-auto text-xs text-neutral-700">{JSON.stringify(
													entry.diff,
													null,
													2
												)}</pre>
										</td>
									</tr>
								{/if}
							{/each}
						</tbody>
					</table>
				</div>

				{#if auditHasMore}
					<div class="mt-4 text-center">
						<button
							type="button"
							disabled={auditLoading}
							class="rounded-md border border-neutral-300 px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
							onclick={() => loadAuditLogs(false)}
						>
							{auditLoading ? 'Loading…' : 'Load more'}
						</button>
					</div>
				{/if}
			{/if}
		{/if}
	{/if}
</div>
