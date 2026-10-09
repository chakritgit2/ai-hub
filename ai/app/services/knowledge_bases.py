"""Knowledge base config resolution + `runtime.kb_documents` repository (PRD §6.6).

Shared by kb_indexer/kb_search/kb_export/main_runtime's `/internal/v1/kb/*` endpoints,
the same split `connections.py`/`connection_secrets.py` use: one module per concern
instead of each caller re-deriving its own SQL.
"""
import re
import uuid
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from dynamiq.connections.connections import PostgreSQL as PostgreSQLConnection
from dynamiq.storages.vector.pgvector.pgvector import PGVectorIndexMethod, PGVectorStore
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import get_settings
from app.core.db import get_company_session
from app.db.tables import kb_documents_table

# `runtime.kbv_{company_id}_{kb_id}` with both UUIDs un-hyphenated - validated before
# ever being interpolated into SQL (even though company_id/kb_id here always come from
# the DB, not request input directly - defense in depth per the table-naming PRD §6.6
# warns never resolve from the request/spec).
_TABLE_NAME_PATTERN = re.compile(r"^kbv_[0-9a-f]{32}_[0-9a-f]{32}$")

_EMBEDDING_DIMENSION = 1536  # must match kb_indexer.py's indexing dimension


@dataclass(frozen=True)
class KnowledgeBaseRow:
    id: str
    name: str
    embedder_connection_id: str
    chunk_size: int
    chunk_overlap: int
    retrieval_mode: str
    alpha: float
    okf_field_map: dict


@dataclass(frozen=True)
class KbDocumentRow:
    id: str
    company_id: str
    kb_id: str
    okf_id: str
    path: str
    category: str | None
    frontmatter: dict
    object_key: str
    content_hash: str
    status: str
    error: str | None
    chunk_count: int
    created_at: datetime
    updated_at: datetime


async def resolve_knowledge_base(company_id: str, kb_id: str) -> KnowledgeBaseRow | None:
    """`None` for both "doesn't exist" and "belongs to another company" (PRD §12's
    "as if it didn't exist" pattern), same as `app.services.connections.resolve_connection`."""
    async with get_company_session(company_id) as session:
        result = await session.execute(
            sa.text(
                "SELECT id, name, embedder_connection_id, chunk_size, chunk_overlap, "
                "retrieval_mode, alpha, okf_field_map "
                "FROM console.resolve_knowledge_base(:kb_id, :company_id)"
            ),
            {"kb_id": uuid.UUID(kb_id), "company_id": uuid.UUID(company_id)},
        )
        row = result.mappings().one_or_none()

    if row is None:
        return None

    return KnowledgeBaseRow(
        id=str(row["id"]),
        name=row["name"],
        embedder_connection_id=str(row["embedder_connection_id"]),
        chunk_size=row["chunk_size"],
        chunk_overlap=row["chunk_overlap"],
        retrieval_mode=row["retrieval_mode"],
        alpha=float(row["alpha"]),
        okf_field_map=row["okf_field_map"],
    )


def kb_vector_table_name(company_id: str, kb_id: str) -> str:
    table_name = f"kbv_{uuid.UUID(company_id).hex}_{uuid.UUID(kb_id).hex}"
    if not _TABLE_NAME_PATTERN.match(table_name):
        raise ValueError(f"unexpected KB vector table name: {table_name!r}")  # pragma: no cover - defense in depth
    return table_name


def open_kb_vector_store(table_name: str) -> PGVectorStore:
    """Opens the per-KB pgvector table for reading - raises `VectorStoreException` (via
    `PGVectorStore`'s own constructor, `create_if_not_exist=False`) if no document has
    finished indexing into it yet. Synchronous (real DB I/O) - callers on the asyncio event
    loop must wrap this in `asyncio.to_thread`, same as `app.services.kb_search` does.

    Shared by `kb_search.search_knowledge_base` (the standalone `/kb/search` endpoint) and
    `app.services.compiler` (an agent's `VectorStoreRetriever` tool), so "is this KB
    actually indexed" is one check, not two independently-maintained ones."""
    settings = get_settings()
    connection = PostgreSQLConnection(
        host=settings.MEMORY_DB_HOST,
        port=settings.MEMORY_DB_PORT,
        database=settings.MEMORY_DB_NAME,
        user=settings.MEMORY_DB_USER,
        password=settings.MEMORY_DB_PASSWORD,
    )
    return PGVectorStore(
        connection=connection,
        schema_name="runtime",
        table_name=table_name,
        dimension=_EMBEDDING_DIMENSION,
        create_if_not_exist=False,
        index_method=PGVectorIndexMethod.EXACT,
    )


def _row_to_document(row: dict) -> KbDocumentRow:
    return KbDocumentRow(
        id=str(row["id"]),
        company_id=str(row["company_id"]),
        kb_id=str(row["kb_id"]),
        okf_id=row["okf_id"],
        path=row["path"],
        category=row["category"],
        frontmatter=row["frontmatter"],
        object_key=row["object_key"],
        content_hash=row["content_hash"],
        status=row["status"],
        error=row["error"],
        chunk_count=row["chunk_count"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def get_kb_document(company_id: str, document_id: str) -> KbDocumentRow | None:
    async with get_company_session(company_id) as session:
        row = (
            await session.execute(
                sa.select(kb_documents_table).where(kb_documents_table.c.id == uuid.UUID(document_id))
            )
        ).mappings().one_or_none()
    return None if row is None else _row_to_document(dict(row))


async def find_kb_document_by_okf_id(company_id: str, kb_id: str, okf_id: str) -> KbDocumentRow | None:
    async with get_company_session(company_id) as session:
        row = (
            await session.execute(
                sa.select(kb_documents_table).where(
                    kb_documents_table.c.kb_id == uuid.UUID(kb_id),
                    kb_documents_table.c.okf_id == okf_id,
                )
            )
        ).mappings().one_or_none()
    return None if row is None else _row_to_document(dict(row))


async def list_kb_documents(company_id: str, kb_id: str) -> list[KbDocumentRow]:
    async with get_company_session(company_id) as session:
        rows = (
            await session.execute(
                sa.select(kb_documents_table)
                .where(kb_documents_table.c.kb_id == uuid.UUID(kb_id))
                .order_by(kb_documents_table.c.path)
            )
        ).mappings().all()
    return [_row_to_document(dict(row)) for row in rows]


async def upsert_kb_document(
    *,
    company_id: str,
    kb_id: str,
    okf_id: str,
    path: str,
    category: str | None,
    frontmatter: dict,
    object_key: str,
    content_hash: str,
) -> str:
    """Inserts a new `kb_documents` row, or updates the existing one for
    `(kb_id, okf_id)` and resets it to `status='queued'` for re-indexing. Returns the
    document id either way."""
    async with get_company_session(company_id) as session:
        stmt = pg_insert(kb_documents_table).values(
            company_id=uuid.UUID(company_id),
            kb_id=uuid.UUID(kb_id),
            okf_id=okf_id,
            path=path,
            category=category,
            frontmatter=frontmatter,
            object_key=object_key,
            content_hash=content_hash,
            status="queued",
            chunk_count=0,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["kb_id", "okf_id"],
            set_={
                "path": stmt.excluded.path,
                "category": stmt.excluded.category,
                "frontmatter": stmt.excluded.frontmatter,
                "object_key": stmt.excluded.object_key,
                "content_hash": stmt.excluded.content_hash,
                "status": "queued",
                "error": None,
                "chunk_count": 0,
                "updated_at": sa.func.now(),
            },
        ).returning(kb_documents_table.c.id)
        document_id = (await session.execute(stmt)).scalar_one()
    return str(document_id)


async def update_kb_document_status(
    company_id: str, document_id: str, *, status: str, chunk_count: int = 0, error: str | None = None
) -> None:
    async with get_company_session(company_id) as session:
        await session.execute(
            kb_documents_table.update()
            .where(kb_documents_table.c.id == uuid.UUID(document_id))
            .values(status=status, chunk_count=chunk_count, error=error, updated_at=sa.func.now())
        )


async def delete_kb_documents(company_id: str, kb_id: str) -> None:
    async with get_company_session(company_id) as session:
        await session.execute(kb_documents_table.delete().where(kb_documents_table.c.kb_id == uuid.UUID(kb_id)))
