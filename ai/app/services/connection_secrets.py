"""runtime.connection_secrets read/write (PRD §7.4/§7.7)."""
import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.crypto import decrypt_with_dek
from app.core.db import get_company_session
from app.db.tables import connection_secrets_table
from app.services.company_keys import get_or_create_company_dek


@dataclass(frozen=True)
class ConnectionSecretRow:
    ciphertext: bytes
    nonce: bytes
    dek_version: int


async def upsert_connection_secret(
    *, company_id: str, connection_id: str, ciphertext: bytes, nonce: bytes, dek_version: int
) -> None:
    async with get_company_session(company_id) as session:
        stmt = pg_insert(connection_secrets_table).values(
            connection_id=uuid.UUID(connection_id),
            company_id=uuid.UUID(company_id),
            ciphertext=ciphertext,
            nonce=nonce,
            dek_version=dek_version,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["connection_id"],
            set_={
                "ciphertext": stmt.excluded.ciphertext,
                "nonce": stmt.excluded.nonce,
                "dek_version": stmt.excluded.dek_version,
            },
        )
        await session.execute(stmt)


async def get_connection_secret(company_id: str, connection_id: str) -> ConnectionSecretRow | None:
    async with get_company_session(company_id) as session:
        row = (
            await session.execute(
                sa.select(
                    connection_secrets_table.c.ciphertext,
                    connection_secrets_table.c.nonce,
                    connection_secrets_table.c.dek_version,
                ).where(connection_secrets_table.c.connection_id == uuid.UUID(connection_id))
            )
        ).mappings().one_or_none()

    return None if row is None else ConnectionSecretRow(**row)


async def decrypt_connection_secret(company_id: str, connection_id: str) -> str | None:
    """The stored secret in plaintext, or `None` if nothing has been stored for this
    connection yet. Shared by `testConnection` and Playground's agent-version resolution
    (`app.services.runtime`) - both need the same decrypt-with-the-company's-own-DEK
    dance, previously duplicated inline in `app.main_runtime::testConnection`."""
    stored = await get_connection_secret(company_id, connection_id)
    if stored is None:
        return None

    company_dek = await get_or_create_company_dek(company_id)
    return decrypt_with_dek(stored.ciphertext, stored.nonce, company_dek.dek).decode()
