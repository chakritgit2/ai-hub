import psycopg
import pytest

import app.services.deployments as deployments_module
from app.core.config import get_settings
from app.services.deployments import resolve_deployment

from .markers import requires_postgres


@pytest.fixture(autouse=True)
def _clear_deployment_cache():
    """`resolve_deployment`'s cache is module-level (per-process, by design - see its
    docstring) - clear it around every test so one test's cached row can never leak into
    another's assertions, even though every test here uses a fresh uuid4 deployment_id."""
    deployments_module._cache.clear()
    yield
    deployments_module._cache.clear()


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
    """Mirrors console-api's own convention (every mutator bumps config_version) without
    going through console-api itself - this is a Python test, not a PHP one."""
    with _connect() as conn:
        conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
        conn.execute(
            "UPDATE console.deployments SET rate_limit_per_min = %s, config_version = config_version + 1 "
            "WHERE id = %s",
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
async def test_resolve_deployment_refetches_after_ttl_when_version_changed(
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
async def test_resolve_deployment_skips_full_refetch_when_version_unchanged_after_ttl(
    company_ids, make_company, resolvable_agent_version, make_deployment, monkeypatch
):
    """Past the TTL with no real change, `resolve_deployment` should only pay for the
    cheap `config_version` check, never the full-row query - this is the whole
    performance point of caching the row at all (see deployments.py's module docstring)."""
    company_id = company_ids()
    make_company(company_id)
    version_id = await resolvable_agent_version(company_id)
    deployment_id, _slug = make_deployment(company_id, version_id)

    await resolve_deployment(company_id, deployment_id)
    _advance_past_ttl(monkeypatch)

    async def _fail(*_args, **_kwargs):
        raise AssertionError("full row refetch must not happen when config_version is unchanged")

    monkeypatch.setattr(deployments_module, "_fetch_deployment_row", _fail)

    result = await resolve_deployment(company_id, deployment_id)
    assert result is not None


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

    monkeypatch.setattr(deployments_module, "_fetch_config_version", _boom)

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

    monkeypatch.setattr(deployments_module, "_fetch_config_version", _boom)

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

    monkeypatch.setattr(deployments_module, "_fetch_config_version", _boom)

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
