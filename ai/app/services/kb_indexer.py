"""Knowledge base indexing service (PRD §6.6, phase 2).

Splits an OKF document's body with `MarkdownHeaderSplitterComponent` (re-splitting any
chunk still over `chunk_size` with `RecursiveCharacterSplitterComponent`), embeds the
chunks with the KB owner's connection, and stores them in a per-KB pgvector table
(`runtime.kbv_{company_id}_{kb_id}`) via `dynamiq.storages.vector.pgvector.PGVectorStore`.

The retriever (kb_search.py) resolves the table only from the KB owned by the company
in context, never from the request/spec - same discipline as the rest of this module.
"""
import asyncio

from dynamiq.components.splitters.markdown_header import MarkdownHeaderSplitterComponent
from dynamiq.components.splitters.recursive_character import RecursiveCharacterSplitterComponent
from dynamiq.connections.connections import PostgreSQL as PostgreSQLConnection
from dynamiq.storages.vector.pgvector.pgvector import PGVectorIndexMethod, PGVectorStore
from dynamiq.types import Document

from app.core.config import get_settings
from app.core.storage import get_object
from app.integrations.dynamiq_adapter import build_embedder
from app.integrations.okf import parse_okf, resolve_okf_metadata
from app.integrations.thai_tokenizer import tokenize
from app.services.connection_secrets import decrypt_connection_secret
from app.services.connections import resolve_connection
from app.services.kb_hybrid import ensure_search_text_column, write_search_text_batch
from app.services.knowledge_bases import (
    get_kb_document,
    kb_vector_table_name,
    resolve_knowledge_base,
    update_kb_document_status,
)

_EMBEDDING_DIMENSION = 1536  # text-embedding-3-small - fixed for MVP (PRD §6.6 defers
# per-KB embedder model choice beyond "which connection"; dimension would need to come
# from config to support a different model/dimension later).


def _open_vector_store(table_name: str) -> PGVectorStore:
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
        create_if_not_exist=True,
        index_method=PGVectorIndexMethod.EXACT,
    )


def _split_into_chunks(body: str, chunk_size: int, chunk_overlap: int, base_metadata: dict) -> list[Document]:
    header_chunks = MarkdownHeaderSplitterComponent().run(documents=[Document(content=body, metadata=base_metadata)])[
        "documents"
    ]
    # Re-split any chunk still over chunk_size - a rough character-length budget for
    # MVP, not a real token count (PRD's "~800 tokens" is an estimate either way).
    recursive_splitter = RecursiveCharacterSplitterComponent(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    return recursive_splitter.run(documents=header_chunks)["documents"]


async def index_kb_document(company_id: str, kb_id: str, document_id: str) -> dict:
    try:
        document = await get_kb_document(company_id, document_id)
        if document is None:
            return {"status": "failed", "chunk_count": 0, "error": "kb_document_not_found"}

        kb = await resolve_knowledge_base(company_id, kb_id)
        if kb is None:
            error = "knowledge_base_not_found"
            await update_kb_document_status(company_id, document_id, status="failed", error=error)
            return {"status": "failed", "chunk_count": 0, "error": error}

        await update_kb_document_status(company_id, document_id, status="processing")

        raw_markdown = (await asyncio.to_thread(get_object, document.object_key)).decode("utf-8")
        parsed = parse_okf(raw_markdown)
        metadata = resolve_okf_metadata(parsed["frontmatter"], kb.okf_field_map, document.path, parsed["body"])

        base_metadata = {"kb_id": kb_id, "document_id": document_id, "frontmatter": metadata}
        chunks = _split_into_chunks(parsed["body"], kb.chunk_size, kb.chunk_overlap, base_metadata)
        if not chunks:
            error = "no_content_after_splitting"
            await update_kb_document_status(company_id, document_id, status="failed", error=error)
            return {"status": "failed", "chunk_count": 0, "error": error}

        for index, chunk in enumerate(chunks):
            chunk.id = f"{document_id}:{index}"

        connection_row = await resolve_connection(company_id, kb.embedder_connection_id)
        if connection_row is None:
            error = "embedder_connection_not_found"
            await update_kb_document_status(company_id, document_id, status="failed", error=error)
            return {"status": "failed", "chunk_count": 0, "error": error}

        secret = await decrypt_connection_secret(company_id, kb.embedder_connection_id)
        if secret is None:
            error = "embedder_connection_secret_not_found"
            await update_kb_document_status(company_id, document_id, status="failed", error=error)
            return {"status": "failed", "chunk_count": 0, "error": error}

        _connection, embedder = build_embedder(connection_row.type, {"api_key": secret})
        embedded = await embedder.embed_documents_async(chunks)
        embedded_chunks = embedded["documents"]

        table_name = kb_vector_table_name(company_id, kb_id)
        store = await asyncio.to_thread(_open_vector_store, table_name)
        try:
            if document.chunk_count > 0:
                # Re-indexing: PGVectorStore.write_documents is an upsert keyed by chunk
                # id ("{document_id}:{i}") - if this version produces fewer chunks than
                # the previous one, the extra old ids would never be overwritten and
                # would keep surfacing in search results. Delete the previous chunk ids
                # for this document before writing the new set.
                stale_chunk_ids = [f"{document_id}:{i}" for i in range(document.chunk_count)]
                await asyncio.to_thread(store.delete_documents, stale_chunk_ids)
            await asyncio.to_thread(store.write_documents, embedded_chunks)

            # Pre-tokenized keyword text for hybrid search (PRD §6.6) - written
            # unconditionally, not just for retrieval_mode == "hybrid": cheap, and means a
            # KB switched to hybrid *after* indexing only needs its normal content-hash-
            # triggered re-index, not a separate backfill feature.
            await asyncio.to_thread(ensure_search_text_column, table_name)
            search_text_by_id = {chunk.id: " ".join(tokenize(chunk.content)) for chunk in embedded_chunks}
            await asyncio.to_thread(write_search_text_batch, table_name, search_text_by_id)
        finally:
            await asyncio.to_thread(store.close)

        await update_kb_document_status(company_id, document_id, status="ready", chunk_count=len(embedded_chunks))
        return {"status": "ready", "chunk_count": len(embedded_chunks), "error": None}
    except Exception as exc:  # noqa: BLE001 - indexing failures must never crash the
        # ARQ job loop; they're recorded on the document row instead (PRD §6.6 status
        # queued -> processing -> ready|failed).
        await update_kb_document_status(company_id, document_id, status="failed", error=str(exc))
        return {"status": "failed", "chunk_count": 0, "error": str(exc)}
