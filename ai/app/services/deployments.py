"""Resolves a company's own `console.deployments` row for the gateway (PRD §6.3).

Queries `console.deployments` directly, scoped by `get_company_session`'s `SET LOCAL
app.company_id` + the table's own RLS policy - unlike `console.connections`/
`console.agent_versions`, no SECURITY DEFINER function was needed here: verified live
that `ai_app` can read this table and that RLS correctly isolates by company_id (a
cross-company id resolves to `None`, same as a nonexistent one).

`resolve_deployment` also implements the PRD §4.4/§4.5 gateway config cache: an
in-process (per ai-gateway/ai-runtime pod) cache keyed by `(company_id, deployment_id)`,
revalidated against the DB every `_CACHE_TTL_SECONDS`, with a DB error during
revalidation falling back to the last-known row instead of failing the request (the
"gateway keeps serving from cached config" brief-failover tolerance). No cross-pod
invalidation - bounded staleness only, same tradeoff the PRD's own "lost notification"
sentence accepts.

Every revalidation is serialized per key through `_get_lock` - found by code-review to be
necessary, not just an efficiency nicety: without it, concurrent callers for the same key
each independently fetch and then unconditionally overwrite `_cache[key]`, so a slower
fetch that started before a config change can land *after* a faster one that already
picked up the change, silently regressing the cached row back to stale data (worse,
briefly "resurrecting" an already-deleted deployment if its delete raced a read that
started just before). The lock also collapses what would otherwise be N redundant DB
round trips (one per concurrent caller) into one.

Revalidation always refetches the full row rather than cheaply comparing `config_version`
first and only refetching when it changed (console-api still bumps it on every mutation -
`DeploymentsController.php`/`AgentsController.php`/`WorkflowsController.php` - the column
just isn't read here any more). Deliberate, re-litigated once already via code-review:
the two-phase version-check-then-maybe-refetch design pays 2 DB round trips on a cold
cache or an actual config change (exactly the cases that matter most) and only saves 1 in
the common "nothing changed" case - and even then it only saves transferring a handful of
extra columns on an already-open connection, not an extra round trip. Round-trip count
(what actually dominates latency here) is equal-or-better with a single always-fetch
query in every case, so that's what this does; `config_version` remains useful as
console-api's own audit/ordering signal even though this cache no longer reads it."""
import asyncio
import time
import uuid
import weakref
from dataclasses import dataclass, replace

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from app.core.db import get_company_session, get_sessionmaker

_CACHE_TTL_SECONDS = 5.0
# Bounds the cache's worst-case memory growth over a long-running pod's lifetime - entries
# are otherwise only ever removed when a *later* lookup for that exact key discovers the
# deployment is gone, so a deployment that's deleted (or just stops being queried) and
# never looked up again would linger forever (found by code-review). Generous on purpose:
# this project's realistic scale (companies × deployments per company) is nowhere near
# this number - it exists as a backstop against a pathological case (e.g. a high-churn
# test tenant), not a tuning knob for normal operation.
_CACHE_MAX_ENTRIES = 2000


@dataclass(frozen=True)
class DeploymentRow:
    id: str
    # Exactly one of these two is set (DB CHECK constraint enforces it) - agent_version_id
    # for an Agent-backed deployment, workflow_version_id for a Workflow-backed one (PRD
    # §6.3/§6.9 phase 4 workflow deployment exposure).
    agent_version_id: str | None
    workflow_version_id: str | None
    rate_limit_per_min: int
    daily_token_limit: int | None
    daily_cost_limit_usd: float | None
    allowed_origins: list[str]
    guardrail_overrides: dict
    output_mode: str
    allow_write_tools: bool
    conversation_ttl_days: int
    enabled: bool


@dataclass
class _CacheEntry:
    row: DeploymentRow
    last_checked: float
    # Separate from `last_checked` (when this entry was last revalidated against the DB,
    # used for TTL staleness) - this tracks when it was last actually *read*, updated on
    # every cache hit too, not just on a real refetch. `_evict_oldest_if_over_capacity`
    # needs this distinction: a hot deployment revalidated 4.9s ago but read constantly
    # in between would otherwise look "older" than a deployment fetched once a moment ago
    # and never read again, and get evicted first - the wrong way around (found by
    # code-review).
    last_accessed: float


_cache: dict[tuple[str, str], _CacheEntry] = {}
# One lock per key. Uses a WeakValueDictionary, not a plain dict, so a key's Lock is
# automatically dropped once nothing holds a reference to it any more (i.e. no caller is
# currently inside `async with _get_lock(key):` for it) - the safe way to bound this,
# found by code-review after first shipping this as a plain dict with a comment
# explaining why *manual* eviction would be unsafe: popping a dict entry while the lock
# it points to is still held could let a new caller construct a second, independent Lock
# for the same key and run concurrently with the first, defeating the whole point of
# locking per key. A `WeakValueDictionary` sidesteps that entirely - it only ever drops
# an entry when the Lock object itself is already unreferenced and garbage, so a second
# Lock for the same key can never coexist with a still-live first one.
_locks: "weakref.WeakValueDictionary[tuple[str, str], asyncio.Lock]" = weakref.WeakValueDictionary()


def _get_lock(key: tuple[str, str]) -> asyncio.Lock:
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


def _copy_for_caller(row: DeploymentRow) -> DeploymentRow:
    """Defensive copy of `row`'s two mutable fields before handing it to a caller.
    `@dataclass(frozen=True)` only blocks reassigning `DeploymentRow`'s own top-level
    fields - it does not deep-freeze `allowed_origins`/`guardrail_overrides`. Before this
    cache existed, every call built a brand new `DeploymentRow` from a fresh query, so
    even a buggy in-place mutation by some future caller (e.g. `deployment.
    allowed_origins.append(...)`) would only ever corrupt that one call's throwaway
    object. Now the exact same row instance would otherwise be shared by every caller
    within the cache's TTL window - returning a copy here restores the old "every caller
    gets its own object" guarantee (found by code-review; no current caller actually
    mutates these fields, but nothing stops a future one from doing so by mistake)."""
    return replace(
        row, allowed_origins=list(row.allowed_origins), guardrail_overrides=dict(row.guardrail_overrides)
    )


def _evict_oldest_if_over_capacity() -> None:
    if len(_cache) <= _CACHE_MAX_ENTRIES:
        return
    oldest_key = min(_cache, key=lambda key: _cache[key].last_accessed)
    _cache.pop(oldest_key, None)


async def resolve_deployment_id_by_slug(company_id: str, slug: str) -> str | None:
    """Maps a URL slug to its deployment id, scoped to `company_id` - used only to
    cross-check a session token's embedded `deployment_id` claim against the slug the
    browser actually called (PRD §7.5), never to authenticate on its own (the token
    itself, verified separately, is what proves `company_id`)."""
    async with get_company_session(company_id) as session:
        result = await session.execute(
            sa.text("SELECT id FROM console.deployments WHERE slug = :slug AND company_id = :company_id"),
            {"slug": slug, "company_id": uuid.UUID(company_id)},
        )
        row = result.one_or_none()

    return str(row[0]) if row is not None else None


@dataclass(frozen=True)
class DeploymentCors:
    allowed_origins: list[str]
    enabled: bool


# Separate from `_cache`/`_locks` above (keyed by plain `slug`, not `(company_id,
# deployment_id)`) - serves a structurally different, unauthenticated caller
# (`resolve_deployment_cors` below has no prior auth to even reach a company_id with, see
# its own docstring). A short TTL cache here isn't just an efficiency nicety like
# `resolve_deployment`'s: found by code-review that without one, this function is a fully
# unauthenticated, uncached, real-Postgres-query endpoint reachable by anyone who can send
# an HTTP request with nothing but a guessed/known slug - no rate limiting exists anywhere
# else in `app.main_gateway` to stop that. Negative results (unknown slug) are cached too,
# for the same reason - a slug-enumeration probe is the more realistic abuse shape here
# than repeated hits on one real slug.
_CORS_CACHE_TTL_SECONDS = 5.0
_CORS_CACHE_MAX_ENTRIES = 2000
_cors_cache: dict[str, tuple[DeploymentCors | None, float]] = {}
_cors_locks: "weakref.WeakValueDictionary[str, asyncio.Lock]" = weakref.WeakValueDictionary()


def _get_cors_lock(slug: str) -> asyncio.Lock:
    lock = _cors_locks.get(slug)
    if lock is None:
        lock = asyncio.Lock()
        _cors_locks[slug] = lock
    return lock


async def resolve_deployment_cors(slug: str) -> DeploymentCors | None:
    """Resolves just enough (`allowed_origins`, `enabled`) to answer a real browser's CORS
    preflight (`OPTIONS`, PRD Goal #6) for `slug` - called with **no prior auth**, by
    design: a preflight request never carries the caller's eventual `Authorization`
    header (CORS spec), so there is no `company_id` yet to scope a normal
    `get_company_session` call with. Not company-scoped for the same reason
    `app.services.api_keys.resolve_gateway_api_key` isn't - opens a plain session directly
    from the sessionmaker and leans on `console.resolve_deployment_cors`
    (`db/migrations/post/029_resolve_deployment_cors.sql`) being `SECURITY DEFINER` to
    read past RLS itself. `None` for an unknown slug - indistinguishable from any other
    "nothing to allow" case, same as every other slug/id resolver in this codebase.

    Cached per-slug for `_CORS_CACHE_TTL_SECONDS` (see `_cors_cache`'s own comment for
    why this one isn't optional the way a cache usually is) - locked per key purely to
    collapse a thundering herd of concurrent first-callers for the same cold slug into one
    DB round trip, not for any data-correctness reason `resolve_deployment`'s own lock
    has to worry about (this value changes rarely and a few seconds of staleness on a CORS
    allowlist is immaterial)."""
    cached = _cors_cache.get(slug)
    if cached is not None and (time.monotonic() - cached[1]) < _CORS_CACHE_TTL_SECONDS:
        return cached[0]

    async with _get_cors_lock(slug):
        cached = _cors_cache.get(slug)
        if cached is not None and (time.monotonic() - cached[1]) < _CORS_CACHE_TTL_SECONDS:
            return cached[0]

        value = await _fetch_deployment_cors(slug)

        if slug not in _cors_cache and len(_cors_cache) >= _CORS_CACHE_MAX_ENTRIES:
            # Bounds worst-case memory from an attacker probing many distinct/garbage
            # slugs - oldest-inserted eviction is enough here, unlike resolve_deployment's
            # last-accessed tracking: a stampede of fresh nonexistent slugs is the
            # realistic growth pattern for this cache, not a few legitimately hot ones.
            _cors_cache.pop(next(iter(_cors_cache)), None)
        _cors_cache[slug] = (value, time.monotonic())
        return value


async def _fetch_deployment_cors(slug: str) -> DeploymentCors | None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(
            sa.text("SELECT allowed_origins, enabled FROM console.resolve_deployment_cors(:slug)"),
            {"slug": slug},
        )
        row = result.mappings().one_or_none()

    if row is None:
        return None

    return DeploymentCors(allowed_origins=row["allowed_origins"], enabled=row["enabled"])


async def _fetch_deployment_row(company_id: str, deployment_id: str) -> DeploymentRow | None:
    async with get_company_session(company_id) as session:
        result = await session.execute(
            sa.text(
                "SELECT id, agent_version_id, workflow_version_id, rate_limit_per_min, daily_token_limit, "
                "daily_cost_limit_usd, allowed_origins, guardrail_overrides, output_mode, allow_write_tools, "
                "conversation_ttl_days, enabled "
                "FROM console.deployments WHERE id = :id AND company_id = :company_id"
            ),
            {"id": uuid.UUID(deployment_id), "company_id": uuid.UUID(company_id)},
        )
        row = result.mappings().one_or_none()

    if row is None:
        return None

    return DeploymentRow(
        id=str(row["id"]),
        agent_version_id=str(row["agent_version_id"]) if row["agent_version_id"] is not None else None,
        workflow_version_id=str(row["workflow_version_id"]) if row["workflow_version_id"] is not None else None,
        rate_limit_per_min=row["rate_limit_per_min"],
        daily_token_limit=row["daily_token_limit"],
        daily_cost_limit_usd=float(row["daily_cost_limit_usd"]) if row["daily_cost_limit_usd"] is not None else None,
        allowed_origins=row["allowed_origins"],
        guardrail_overrides=row["guardrail_overrides"],
        output_mode=row["output_mode"],
        allow_write_tools=row["allow_write_tools"],
        conversation_ttl_days=row["conversation_ttl_days"],
        enabled=row["enabled"],
    )


def _is_fresh(entry: _CacheEntry | None) -> bool:
    return entry is not None and (time.monotonic() - entry.last_checked) < _CACHE_TTL_SECONDS


async def resolve_deployment(company_id: str, deployment_id: str) -> DeploymentRow | None:
    key = (company_id, deployment_id)
    cached = _cache.get(key)
    if _is_fresh(cached):
        cached.last_accessed = time.monotonic()
        return _copy_for_caller(cached.row)

    async with _get_lock(key):
        # Re-check after acquiring the lock - another coroutine may have already
        # refreshed this key while we were waiting for it (the thundering-herd case),
        # in which case there's nothing left to do.
        cached = _cache.get(key)
        if _is_fresh(cached):
            cached.last_accessed = time.monotonic()
            return _copy_for_caller(cached.row)

        try:
            row = await _fetch_deployment_row(company_id, deployment_id)
        except SQLAlchemyError:
            if cached is not None:
                # Also push last_checked forward, not just serve the stale row -
                # otherwise a sustained outage (not just a "brief" one) makes every
                # single request retry the DB immediately again instead of backing off
                # for another TTL window, turning the cache into a retry storm against
                # an already-struggling DB.
                now = time.monotonic()
                cached.last_checked = now
                cached.last_accessed = now
                return _copy_for_caller(cached.row)
            raise

        if row is None:
            _cache.pop(key, None)
            return None

        now = time.monotonic()
        _cache[key] = _CacheEntry(row=row, last_checked=now, last_accessed=now)
        _evict_oldest_if_over_capacity()
        return _copy_for_caller(row)
