"""runtime.tool_secrets read/write - the `auth_mode: "service"` counterpart to
`app.services.connection_secrets`, same encryption scheme and trust boundary."""
import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.crypto import decrypt_with_dek
from app.core.db import get_company_session
from app.db.tables import tool_secrets_table
from app.services.company_keys import get_or_create_company_dek


@dataclass(frozen=True)
class ToolSecretRow:
    ciphertext: bytes
    nonce: bytes
    dek_version: int


async def upsert_tool_secret(
    *, company_id: str, tool_id: str, ciphertext: bytes, nonce: bytes, dek_version: int
) -> None:
    async with get_company_session(company_id) as session:
        stmt = pg_insert(tool_secrets_table).values(
            tool_id=uuid.UUID(tool_id),
            company_id=uuid.UUID(company_id),
            ciphertext=ciphertext,
            nonce=nonce,
            dek_version=dek_version,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["tool_id"],
            set_={
                "ciphertext": stmt.excluded.ciphertext,
                "nonce": stmt.excluded.nonce,
                "dek_version": stmt.excluded.dek_version,
            },
        )
        await session.execute(stmt)


async def get_tool_secret(company_id: str, tool_id: str) -> ToolSecretRow | None:
    async with get_company_session(company_id) as session:
        row = (
            await session.execute(
                sa.select(
                    tool_secrets_table.c.ciphertext,
                    tool_secrets_table.c.nonce,
                    tool_secrets_table.c.dek_version,
                ).where(tool_secrets_table.c.tool_id == uuid.UUID(tool_id))
            )
        ).mappings().one_or_none()

    return None if row is None else ToolSecretRow(**row)


async def delete_tool_secret(company_id: str, tool_id: str) -> None:
    """Best-effort cleanup called when the owning `console.tools` row is deleted
    (`ToolsController::deleteTool`) - a no-op if no secret was ever stored, same
    convention as `app.services.connection_secrets.delete_connection_secret`."""
    async with get_company_session(company_id) as session:
        await session.execute(
            sa.delete(tool_secrets_table).where(tool_secrets_table.c.tool_id == uuid.UUID(tool_id))
        )


async def decrypt_tool_secret(company_id: str, tool_id: str) -> str | None:
    """The stored secret in plaintext, or `None` if nothing has been stored for this
    tool yet - `None` is a normal, fail-open outcome here (unlike a connection, an
    `auth_mode: service` tool is not required to have a secret at all)."""
    stored = await get_tool_secret(company_id, tool_id)
    if stored is None:
        return None

    company_dek = await get_or_create_company_dek(company_id)
    return decrypt_with_dek(stored.ciphertext, stored.nonce, company_dek.dek).decode()
