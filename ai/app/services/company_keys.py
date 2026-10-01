"""Per-company data key (DEK) lookup/creation (PRD §7.7/§8.2).

get_or_create_company_dek() is the only sanctioned way for other modules to obtain a
company's raw DEK bytes — callers never touch `runtime.company_keys` directly.
"""
import os
import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import master_key_bytes
from app.core.crypto import unwrap_dek, wrap_dek
from app.core.db import get_company_session
from app.db.tables import company_keys_table


@dataclass(frozen=True)
class CompanyDek:
    dek: bytes
    dek_version: int  # == company_keys.master_key_version for this company (no DEK
    # rotation mechanism exists yet — see app/core/crypto.py's module docstring).


async def get_or_create_company_dek(company_id: str) -> CompanyDek:
    master_key = master_key_bytes()
    company_uuid = uuid.UUID(company_id)

    async with get_company_session(company_id) as session:
        existing = (
            await session.execute(
                sa.select(company_keys_table.c.wrapped_dek, company_keys_table.c.master_key_version).where(
                    company_keys_table.c.company_id == company_uuid
                )
            )
        ).mappings().one_or_none()
        if existing is not None:
            return CompanyDek(
                dek=unwrap_dek(existing["wrapped_dek"], master_key),
                dek_version=existing["master_key_version"],
            )

        dek = os.urandom(32)
        wrapped = wrap_dek(dek, master_key)
        # ON CONFLICT DO NOTHING: two concurrent first-time callers for the same
        # brand-new company would otherwise both see "no row" and race on the PK insert —
        # same TOCTOU discipline as AgentsController::publishAgentVersion's
        # `AND is_published = false` guard.
        inserted = (
            await session.execute(
                pg_insert(company_keys_table)
                .values(company_id=company_uuid, wrapped_dek=wrapped, master_key_version=1)
                .on_conflict_do_nothing(index_elements=["company_id"])
                .returning(company_keys_table.c.master_key_version)
            )
        ).mappings().one_or_none()

        if inserted is not None:
            return CompanyDek(dek=dek, dek_version=inserted["master_key_version"])

        # Lost the race — another concurrent call already created the row; use that one.
        row = (
            await session.execute(
                sa.select(company_keys_table.c.wrapped_dek, company_keys_table.c.master_key_version).where(
                    company_keys_table.c.company_id == company_uuid
                )
            )
        ).mappings().one()
        return CompanyDek(dek=unwrap_dek(row["wrapped_dek"], master_key), dek_version=row["master_key_version"])
