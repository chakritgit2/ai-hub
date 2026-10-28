import asyncio

import psycopg
import pytest

import app.services.deployments as deployments_module
from app.core.config import get_settings
from app.services.deployments import resolve_deployment

from .markers import requires_postgres


@pytest.fixture(autouse=True)
def _clear_deployment_cache():
    """`resolve_deployment`'s cache/locks are module-level (per-process, by design - see
    its docstring) - clear them around every test so one test's cached row/lock can never
    leak into another's assertions, even though every test here uses a fresh uuid4
    deployment_id."""
    deployments_module._cache.clear()
    deployments_module._locks.clear()
    yield
    deployments_module._cache.clear()
    deployments_module._locks.clear()


def _connect() -> psycopg.Connection:
    settings = get_settings()
    return psycopg.connect(
        host=settings.MEMORY_DB_HOST,
        port=settings.MEMORY_DB_PORT,
        dbname=settings.MEMORY_DB_NAME,
        user="console_app",
        password="changeme_local_dev_only",
    )


def _bump_rate_limit(company_id: str, deployment_id: str, rate_limit_per_min: int) -> None:
    with _connect() as conn:
        conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
        conn.execute(
            "UPDATE console.deployments SET rate_limit_per_min = %s WHERE id = %s",
            (rate_limit_per_min, deployment_id),
        )
        conn.commit()


def _delete_deployment(company_id: str, deployment_id: str) -> None:
    with _connect() as conn:
        conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
        conn.execute("DELETE FROM console.deployments WHERE id = %s", (deployment_id,))
        conn.commit()


def _advance_past_ttl(monkeypatch: pytest.MonkeyPatch, seconds: float = deployments_module._CACHE_TTL_SECONDS + 1
                       ) -> None:
    real_monotonic = deployments_module.time.monotonic
    monkeypatch.setattr(deployments_module.time, "monotonic", lambda: real_monotonic() + seconds)


@requires_postgres
async def test_resolve_deployment_returns_none_for_unknown_id(company_ids):
    company_id = company_ids()
    assert await resolve_deployment(company_id, "00000000-0000-0000-0000-000000000000") is None


@requires_postgres
async def test_resolve_deployment_happy_path(
    company_ids, make_company, resolvable_agent_version, make_deployment
):
    company_id = company_ids()
    make_company(company_id)
    version_id = await resolvable_agent_version(company_id)
    deployment_id, _slug = make_deployment(company_id, version_id, rate_limit_per_min=60)

    deployment = await resolve_deployment(company_id, deployment_id)

    assert deployment is not None
    assert deployment.id == deployment_id
    assert deployment.rate_limit_per_min == 60


@requires_postgres
async def test_resolve_deployment_serves_stale_value_within_ttl(
    company_ids, make_company, resolvable_agent_version, make_deployment
):
    """A change made directly in the DB isn't picked up until the cache's TTL elapses -
    this is the whole point of the cache (see deployments.py's module docstring)."""
    company_id = company_ids()
    make_company(company_id)
    version_id = await resolvable_agent_version(company_id)
    deployment_id, _slug = make_deployment(company_id, version_id, rate_limit_per_min=60)

    first = await resolve_deployment(company_id, deployment_id)
    assert first.rate_limit_per_min == 60

    _bump_rate_limit(company_id, deployment_id, 999)

    second = await resolve_deployment(company_id, deployment_id)
    assert second.rate_limit_per_min == 60


@requires_postgres
async def test_resolve_deployment_refetches_after_ttl(
    company_ids, make_company, resolvable_agent_version, make_deployment, monkeypatch
):
    company_id = company_ids()
    make_company(company_id)
    version_id = await resolvable_agent_version(company_id)
    deployment_id, _slug = make_deployment(company_id, version_id, rate_limit_per_min=60)

    await resolve_deployment(company_id, deployment_id)
    _bump_rate_limit(company_id, deployment_id, 999)
    _advance_past_ttl(monkeypatch)

    refreshed = await resolve_deployment(company_id, deployment_id)
    assert refreshed.rate_limit_per_min == 999


@requires_postgres
async def test_resolve_deployment_returns_an_independent_copy_each_call(
    company_ids, make_company, resolvable_agent_version, make_deployment
):
    """A real code-review finding: `DeploymentRow` is `@dataclass(frozen=True)`, which
    only blocks reassigning its own top-level fields, not mutating the mutable
    `allowed_origins`/`guardrail_overrides` collections nested inside it. Before caching
    existed, every call got a fresh row anyway; now the same cached row would otherwise
    be shared across every caller within the TTL window, so a future caller's in-place
    mutation bug (e.g. `.allowed_origins.append(...)`) would corrupt what every other
    concurrent/subsequent caller sees instead of just its own throwaway object."""
    company_id = company_ids()
    make_company(company_id)
    version_id = await resolvable_agent_version(company_id)
    deployment_id, _slug = make_deployment(company_id, version_id, allowed_origins=["https://example.com"])

    first = await resolve_deployment(company_id, deployment_id)
    first.allowed_origins.append("https://evil.example.com")
    first.guardrail_overrides["input"] = ["mutated"]

    second = await resolve_deployment(company_id, deployment_id)
    assert second.allowed_origins == ["https://example.com"]
    assert second.guardrail_overrides == {}


@requires_postgres
async def test_resolve_deployment_falls_back_to_stale_cache_on_db_error(
    company_ids, make_company, resolvable_agent_version, make_deployment, monkeypatch
):
    """The brief-DB-failover tolerance the cache exists for (PRD §4.4/§6.11): a DB error
    while revalidating must not fail a request that already has a cached row to serve."""
    company_id = company_ids()
    make_company(company_id)
    version_id = await resolvable_agent_version(company_id)
    deployment_id, _slug = make_deployment(company_id, version_id, rate_limit_per_min=60)

    cached = await resolve_deployment(company_id, deployment_id)
    assert cached.rate_limit_per_min == 60
    _advance_past_ttl(monkeypatch)

    async def _boom(*_args, **_kwargs):
        from sqlalchemy.exc import SQLAlchemyError

        raise SQLAlchemyError("simulated DB failover")

    monkeypatch.setattr(deployments_module, "_fetch_deployment_row", _boom)

    served_during_outage = await resolve_deployment(company_id, deployment_id)
    assert served_during_outage.rate_limit_per_min == 60


@requires_postgres
async def test_resolve_deployment_backs_off_after_db_error_instead_of_retrying_immediately(
    company_ids, make_company, resolvable_agent_version, make_deployment, monkeypatch
):
    """A real code-review finding: serving the stale row on a DB error without also
    pushing last_checked forward would leave the cache perpetually "past TTL", so every
    request during a *sustained* outage (not just a brief one) retries the DB immediately
    - the opposite of the backoff the cache is meant to provide."""
    company_id = company_ids()
    make_company(company_id)
    version_id = await resolvable_agent_version(company_id)
    deployment_id, _slug = make_deployment(company_id, version_id)

    await resolve_deployment(company_id, deployment_id)
    _advance_past_ttl(monkeypatch)

    call_count = 0

    async def _boom(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        from sqlalchemy.exc import SQLAlchemyError

        raise SQLAlchemyError("simulated DB failover")

    monkeypatch.setattr(deployments_module, "_fetch_deployment_row", _boom)

    await resolve_deployment(company_id, deployment_id)
    assert call_count == 1

    # Still "mid-outage", same monkeypatched now() - a second call right away must be
    # served purely from cache (within the freshly-extended TTL), not retry the DB.
    await resolve_deployment(company_id, deployment_id)
    assert call_count == 1


@requires_postgres
async def test_resolve_deployment_propagates_db_error_when_never_cached(monkeypatch, company_ids):
    """No prior cached row to fall back to (e.g. a cold pod during a DB outage) - the
    error must still surface rather than be swallowed into a silent None."""
    company_id = company_ids()

    async def _boom(*_args, **_kwargs):
        from sqlalchemy.exc import SQLAlchemyError

        raise SQLAlchemyError("simulated DB failover")

    monkeypatch.setattr(deployments_module, "_fetch_deployment_row", _boom)

    from sqlalchemy.exc import SQLAlchemyError

    with pytest.raises(SQLAlchemyError):
        await resolve_deployment(company_id, "00000000-0000-0000-0000-000000000000")


@requires_postgres
async def test_resolve_deployment_evicts_cache_when_deployment_deleted(
    company_ids, make_company, resolvable_agent_version, make_deployment, monkeypatch
):
    company_id = company_ids()
    make_company(company_id)
    version_id = await resolvable_agent_version(company_id)
    deployment_id, _slug = make_deployment(company_id, version_id)

    assert await resolve_deployment(company_id, deployment_id) is not None

    _delete_deployment(company_id, deployment_id)
    _advance_past_ttl(monkeypatch)

    assert await resolve_deployment(company_id, deployment_id) is None


@requires_postgres
async def test_resolve_deployment_concurrent_callers_coalesce_into_one_fetch(
    company_ids, make_company, resolvable_agent_version, make_deployment, monkeypatch
):
    """A real code-review finding (thundering herd): without a per-key lock, N
    concurrent callers hitting a stale/cold cache entry at once each independently fetch
    from the DB and each unconditionally overwrite `_cache[key]` - multiplying DB load
    under exactly the traffic burst a cache is meant to absorb, and (see the next test)
    risking a slower fetch overwriting a faster one's newer result."""
    company_id = company_ids()
    make_company(company_id)
    version_id = await resolvable_agent_version(company_id)
    deployment_id, _slug = make_deployment(company_id, version_id)

    call_count = 0
    real_fetch = deployments_module._fetch_deployment_row

    async def _counting_fetch(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0)  # yield control, like a real DB round trip would
        return await real_fetch(*args, **kwargs)

    monkeypatch.setattr(deployments_module, "_fetch_deployment_row", _counting_fetch)

    results = await asyncio.gather(*(resolve_deployment(company_id, deployment_id) for _ in range(10)))

    assert call_count == 1
    assert all(result is not None and result.id == deployment_id for result in results)


@requires_postgres
async def test_resolve_deployment_slower_concurrent_fetch_never_overwrites_a_fresher_write(
    company_ids, make_company, resolvable_agent_version, make_deployment, monkeypatch
):
    """A real code-review finding: without the per-key lock, two concurrent revalidations
    (e.g. one slow, one fast) for the same key could complete out of order and the
    slower one's stale read would silently overwrite the faster one's newer write -
    or, worse, resurrect an already-deleted deployment. The lock makes this structurally
    impossible: only one fetch for a given key is ever in flight at a time."""
    company_id = company_ids()
    make_company(company_id)
    version_id = await resolvable_agent_version(company_id)
    deployment_id, _slug = make_deployment(company_id, version_id, rate_limit_per_min=1)

    await resolve_deployment(company_id, deployment_id)
    _advance_past_ttl(monkeypatch)

    real_fetch = deployments_module._fetch_deployment_row
    call_count = 0

    async def _slow_then_fast_fetch(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Simulate the first caller's round trip taking long enough for a second,
            # independent trigger (a direct DB mutation here) to land first - with the
            # lock in place, no second *resolve_deployment* call can even start its own
            # fetch until this one finishes and releases the lock.
            await asyncio.sleep(0.05)
        return await real_fetch(*args, **kwargs)

    monkeypatch.setattr(deployments_module, "_fetch_deployment_row", _slow_then_fast_fetch)

    results = await asyncio.gather(
        resolve_deployment(company_id, deployment_id), resolve_deployment(company_id, deployment_id)
    )

    assert call_count == 1
    assert results[0].rate_limit_per_min == results[1].rate_limit_per_min == 1


@requires_postgres
async def test_resolve_deployment_evicts_oldest_entry_once_over_capacity(
    company_ids, make_company, resolvable_agent_version, make_deployment, monkeypatch
):
    """A real code-review finding (3 independent review passes converged on this):
    before this bound existed, a deployment that's deleted (or simply never looked up
    again) stayed cached forever - the only eviction path was a *later* lookup for that
    exact key discovering it's gone. This caps the cache's worst-case memory growth."""
    monkeypatch.setattr(deployments_module, "_CACHE_MAX_ENTRIES", 2)

    company_id = company_ids()
    make_company(company_id)
    version_id = await resolvable_agent_version(company_id)
    deployment_a, _ = make_deployment(company_id, version_id)
    deployment_b, _ = make_deployment(company_id, version_id)
    deployment_c, _ = make_deployment(company_id, version_id)

    await resolve_deployment(company_id, deployment_a)
    await resolve_deployment(company_id, deployment_b)
    assert len(deployments_module._cache) == 2

    await resolve_deployment(company_id, deployment_c)

    assert len(deployments_module._cache) == 2
    assert (company_id, deployment_a) not in deployments_module._cache
    assert (company_id, deployment_b) in deployments_module._cache
    assert (company_id, deployment_c) in deployments_module._cache


@requires_postgres
async def test_resolve_deployment_eviction_picks_the_least_recently_accessed_not_fetched(
    company_ids, make_company, resolvable_agent_version, make_deployment, monkeypatch
):
    """A real code-review finding: eviction must track "least recently *read*", not
    "least recently *revalidated against the DB*" - otherwise a hot deployment that's
    read constantly but only revalidated once every TTL window looks artificially old
    next to a deployment that was fetched once a moment ago and never read again, and
    gets evicted first even though it's the one actually in active use.

    Drives `time.monotonic()` explicitly rather than relying on real wall-clock gaps
    between calls - real DB round trips here are fast enough, and `time.monotonic()`'s
    actual resolution platform-dependent enough, that two calls a few real milliseconds
    apart aren't a reliable enough signal on their own for a `last_accessed` ordering
    assertion."""
    monkeypatch.setattr(deployments_module, "_CACHE_MAX_ENTRIES", 2)
    fake_now = [1000.0]
    monkeypatch.setattr(deployments_module.time, "monotonic", lambda: fake_now[0])

    company_id = company_ids()
    make_company(company_id)
    version_id = await resolvable_agent_version(company_id)
    deployment_a, _ = make_deployment(company_id, version_id)
    deployment_b, _ = make_deployment(company_id, version_id)
    deployment_c, _ = make_deployment(company_id, version_id)

    fake_now[0] = 1000.0
    await resolve_deployment(company_id, deployment_a)
    fake_now[0] = 1001.0
    await resolve_deployment(company_id, deployment_b)
    # A cache hit on `a` (still fresh, no refetch) must still count as "recently used" -
    # it should no longer be the eviction victim even though its last_checked (the DB
    # revalidation timestamp) is older than b's.
    fake_now[0] = 1002.0
    await resolve_deployment(company_id, deployment_a)
    fake_now[0] = 1003.0
    await resolve_deployment(company_id, deployment_c)

    assert (company_id, deployment_a) in deployments_module._cache
    assert (company_id, deployment_b) not in deployments_module._cache
    assert (company_id, deployment_c) in deployments_module._cache


@requires_postgres
async def test_resolve_deployment_locks_do_not_accumulate_once_unused(
    company_ids, make_company, resolvable_agent_version, make_deployment
):
    """A real code-review finding: unlike `_cache` (now capped), `_locks` has no size
    bound of its own - it relies entirely on `_get_lock`'s `WeakValueDictionary` to drop
    a key's `Lock` once nothing holds a reference to it any more (no caller currently
    inside `async with _get_lock(key):` for it). Proves that actually happens in
    practice, not just in theory - a plain `dict` here would grow by one entry per
    distinct deployment ever resolved, for the life of the pod."""
    company_id = company_ids()
    make_company(company_id)
    version_id = await resolvable_agent_version(company_id)

    for _ in range(5):
        deployment_id, _slug = make_deployment(company_id, version_id)
        await resolve_deployment(company_id, deployment_id)

    assert len(deployments_module._locks) == 0
