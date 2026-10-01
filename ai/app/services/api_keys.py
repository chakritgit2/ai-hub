"""Resolves a gateway API key to its owning company + scoped deployment (PRD §7.7).

Goes through `console.resolve_api_key` (db/migrations/post/003_resolve_api_key_function.sql,
updated by 008_api_key_scopes_rls_and_resolve_api_key.sql to actually check
`console.api_key_scopes` - SECURITY DEFINER for the same owner-evaluates-RLS reason as
`app.services.connections`/`app.services.agent_versions`. A key that doesn't exist, one
belonging to another company, and one not scoped to this deployment are all
indistinguishable here (all `None`) - PRD §12/§7.7: "resolves only when the key and
deployment share a company and the deployment is in scope - otherwise 404, as if the
deployment did not exist."
"""
import hashlib
from dataclasses import dataclass

import sqlalchemy as sa

from app.core.db import get_sessionmaker


@dataclass(frozen=True)
class ResolvedApiKey:
    company_id: str
    deployment_id: str
    allowed_ips: list[str]


def hash_gateway_api_key(full_key: str) -> str:
    """Same SHA-256-of-the-full-key scheme console-api's ApiKeysController uses when it
    generates a key - no salt/pepper needed, the key itself has 192 bits of entropy."""
    return hashlib.sha256(full_key.encode()).hexdigest()


async def resolve_gateway_api_key(full_key: str, slug: str) -> ResolvedApiKey | None:
    """Not company-scoped like `get_company_session` (there's no company_id yet - that's
    what we're resolving), so this opens a plain session directly from the sessionmaker
    (not `get_session()`, which is an async-generator FastAPI dependency meant to be
    driven by FastAPI's DI machinery - a manual `async for ... break` over it would leave
    the generator suspended without its `async with` cleanup running reliably);
    `resolve_api_key` is SECURITY DEFINER and enforces its own company/scope matching
    internally."""
    key_hash = hash_gateway_api_key(full_key)

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(
            sa.text(
                "SELECT company_id, deployment_id, allowed_ips FROM console.resolve_api_key(:key_hash, :slug)"
            ),
            {"key_hash": key_hash, "slug": slug},
        )
        row = result.mappings().one_or_none()

    if row is None:
        return None

    return ResolvedApiKey(
        company_id=str(row["company_id"]),
        deployment_id=str(row["deployment_id"]),
        allowed_ips=row["allowed_ips"],
    )
