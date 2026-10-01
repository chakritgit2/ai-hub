from app.services.api_keys import resolve_gateway_api_key

from .markers import requires_postgres


@requires_postgres
async def test_resolve_gateway_api_key_happy_path(
    company_ids, resolvable_agent_version, make_deployment, make_api_key
) -> None:
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    deployment_id, slug = make_deployment(company_id, version_id)
    _key_id, full_key = make_api_key(company_id, [deployment_id])

    resolved = await resolve_gateway_api_key(full_key, slug)

    assert resolved is not None
    assert resolved.company_id == company_id
    assert resolved.deployment_id == deployment_id


@requires_postgres
async def test_resolve_gateway_api_key_wrong_slug_is_none(
    company_ids, resolvable_agent_version, make_deployment, make_api_key
) -> None:
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    deployment_id, _slug = make_deployment(company_id, version_id)
    _key_id, full_key = make_api_key(company_id, [deployment_id])

    assert await resolve_gateway_api_key(full_key, "not-a-real-slug") is None


@requires_postgres
async def test_resolve_gateway_api_key_not_scoped_to_deployment_is_none(
    company_ids, resolvable_agent_version, make_deployment, make_api_key
) -> None:
    """A key that exists, for the right company, but was never scoped to this specific
    deployment must resolve to nothing (PRD §7.7/§8.2) - this is the exact gap
    console.resolve_api_key's own TODO comment used to document before
    console.api_key_scopes existed."""
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    scoped_deployment_id, _scoped_slug = make_deployment(company_id, version_id)
    _other_deployment_id, other_slug = make_deployment(company_id, version_id)
    _key_id, full_key = make_api_key(company_id, [scoped_deployment_id])

    assert await resolve_gateway_api_key(full_key, other_slug) is None


@requires_postgres
async def test_resolve_gateway_api_key_cross_company_is_none(
    company_ids, resolvable_agent_version, make_deployment, make_api_key
) -> None:
    """A key scoped to a deployment under a *different* company than the one the key
    itself belongs to can't happen through normal creation, but a key minted for company
    A must never resolve against company B's deployment slug even if guessed."""
    company_a = company_ids()
    company_b = company_ids()
    version_a = await resolvable_agent_version(company_a)
    version_b = await resolvable_agent_version(company_b)
    _deployment_a, _slug_a = make_deployment(company_a, version_a)
    _deployment_b, slug_b = make_deployment(company_b, version_b)
    _key_id, full_key_a = make_api_key(company_a, [_deployment_a])

    assert await resolve_gateway_api_key(full_key_a, slug_b) is None


@requires_postgres
async def test_resolve_gateway_api_key_unknown_key_is_none(
    company_ids, resolvable_agent_version, make_deployment
) -> None:
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    _deployment_id, slug = make_deployment(company_id, version_id)

    assert await resolve_gateway_api_key("ak_test_totally-made-up", slug) is None


@requires_postgres
async def test_resolve_gateway_api_key_returns_allowed_ips(
    company_ids, resolvable_agent_version, make_deployment, make_api_key
) -> None:
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    deployment_id, slug = make_deployment(company_id, version_id)
    _key_id, full_key = make_api_key(company_id, [deployment_id], allowed_ips=["203.0.113.5"])

    resolved = await resolve_gateway_api_key(full_key, slug)

    assert resolved.allowed_ips == ["203.0.113.5"]


@requires_postgres
async def test_disabled_deployment_does_not_resolve(
    company_ids, resolvable_agent_version, make_deployment, make_api_key
) -> None:
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    deployment_id, slug = make_deployment(company_id, version_id, enabled=False)
    _key_id, full_key = make_api_key(company_id, [deployment_id])

    assert await resolve_gateway_api_key(full_key, slug) is None
