<script lang="ts">
	import { page } from '$app/stores';
	import { currentUser, roleForCompany } from '$lib/stores/auth';
	import { companyId } from '$lib/stores/company';

	interface NavItem {
		label: string;
		href: string;
		phase?: 2 | 3 | 4;
	}

	// PRD §6.9 phase 1/2 — real routes.
	const workspaceItems: NavItem[] = [
		{ label: 'Dashboard', href: '/dashboard' },
		{ label: 'Agents', href: '/agents' },
		{ label: 'Playground', href: '/playground' },
		{ label: 'Connections', href: '/connections' },
		{ label: 'Tools', href: '/tools' },
		{ label: 'Skills', href: '/skills' },
		{ label: 'Knowledge Bases', href: '/knowledge-bases' },
		{ label: 'Runs & Logs', href: '/runs' },
		{ label: 'Deployments', href: '/deployments' },
		{ label: 'API Keys', href: '/api-keys' },
		{ label: 'Settings', href: '/settings' }
	];

	// PRD §6.9 phase 3+ — nav entries only, disabled, no routes exist yet.
	const laterPhaseItems: NavItem[] = [
		{ label: 'Evaluation', href: '/evaluation', phase: 3 },
		{ label: 'Workflows', href: '/workflows', phase: 4 }
	];

	function isActive(href: string): boolean {
		return $page.url.pathname === href || $page.url.pathname.startsWith(`${href}/`);
	}

	$: role = roleForCompany($currentUser, $companyId) ?? 'viewer';
</script>

<nav
	class="flex h-full w-60 shrink-0 flex-col bg-sidebar text-neutral-200"
	aria-label="Primary"
>
	<div class="px-4 py-5">
		<span class="font-mono text-sm font-semibold tracking-wide text-white">Dynamiq Console</span>
	</div>

	<div class="flex-1 overflow-y-auto px-2">
		<p class="mt-2 px-2 text-xs font-semibold uppercase tracking-wider text-neutral-500">
			Workspace
		</p>
		<ul class="mt-1 space-y-0.5">
			{#each workspaceItems as item (item.href)}
				<li>
					<a
						href={item.href}
						aria-current={isActive(item.href) ? 'page' : undefined}
						class="flex items-center rounded-md px-3 py-2 text-sm transition-colors
							{isActive(item.href)
							? 'bg-primary/20 text-white font-medium'
							: 'text-neutral-300 hover:bg-white/5 hover:text-white'}"
					>
						{item.label}
					</a>
				</li>
			{/each}
		</ul>

		<p class="mt-6 px-2 text-xs font-semibold uppercase tracking-wider text-neutral-500">
			Later phases
		</p>
		<ul class="mt-1 space-y-0.5 pb-4">
			{#each laterPhaseItems as item (item.href)}
				<li>
					<span
						class="flex cursor-not-allowed items-center justify-between rounded-md px-3 py-2 text-sm text-neutral-600"
						aria-disabled="true"
						title="Planned for phase {item.phase} — not available yet"
					>
						<span>{item.label}</span>
						<span
							class="rounded border border-neutral-700 px-1.5 py-0.5 font-mono text-[10px] leading-none text-neutral-500"
						>
							P{item.phase}
						</span>
					</span>
				</li>
			{/each}
		</ul>
	</div>

	<div class="border-t border-white/10 px-4 py-3">
		{#if $currentUser}
			<p class="truncate text-sm font-medium text-white">{$currentUser.email}</p>
			<p class="text-xs capitalize text-neutral-400">{role}</p>
		{:else if import.meta.env.DEV}
			<a
				href="http://localhost:8999/"
				class="text-xs text-primary underline underline-offset-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
			>
				Sign in (dev SSO)
			</a>
		{:else}
			<p class="text-xs text-neutral-500">Not signed in</p>
		{/if}
	</div>
</nav>
