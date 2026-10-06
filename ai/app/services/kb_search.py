"""Knowledge base search/retrieval (PRD §6.6, phase 2 - vector-only this round).

Mirrors kb_indexer.py's connection/embedder resolution and PGVectorStore wiring, but
for a single query embedding instead of a batch of document chunks.
"""
import asyncio

from dynamiq.components.retrievers.pgvector import PGVectorDocumentRetriever
from dynamiq.connections.connections import PostgreSQL as PostgreSQLConnection
from dynamiq.storages.vector.exceptions import VectorStoreException
from dynamiq.storages.vector.pgvector.pgvector import PGVectorIndexMethod, PGVectorStore

from app.core.config import get_settings
from app.integrations.dynamiq_adapter import build_embedder
from app.services.connection_secrets import decrypt_connection_secret
from app.services.connections import resolve_connection
from app.services.knowledge_bases import kb_vector_table_name, resolve_knowledge_base

_EMBEDDING_DIMENSION = 1536  # must match kb_indexer.py's indexing dimension


class HybridRetrievalNotImplementedError(ValueError):
    """Raised when searching a KB configured with `retrieval_mode='hybrid'` - vector-only
    retrieval is the only mode implemented this round (see plan)."""


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
        create_if_not_exist=False,
        index_method=PGVectorIndexMethod.EXACT,
    )


async def search_knowledge_base(company_id: str, kb_id: str, query: str, top_k: int = 5) -> list[dict]:
    kb = await resolve_knowledge_base(company_id, kb_id)
    if kb is None:
        raise ValueError("knowledge_base_not_found")

    if kb.retrieval_mode == "hybrid":
        raise HybridRetrievalNotImplementedError("hybrid_retrieval_not_yet_implemented")

    connection_row = await resolve_connection(company_id, kb.embedder_connection_id)
    if connection_row is None:
        raise ValueError("embedder_connection_not_found")

    secret = await decrypt_connection_secret(company_id, kb.embedder_connection_id)
    if secret is None:
        raise ValueError("embedder_connection_secret_not_found")

    _connection, embedder = build_embedder(connection_row.type, {"api_key": secret})
    embedded = await embedder.embed_text_async(query)
    query_embedding = embedded["embedding"]

    table_name = kb_vector_table_name(company_id, kb_id)
    try:
        store = await asyncio.to_thread(_open_vector_store, table_name)
    except VectorStoreException as exc:
        # `create_if_not_exist=False` makes PGVectorStore's constructor itself raise when
        # the per-KB table doesn't exist yet - i.e. no document has finished indexing.
        # A plain ValueError (not an unhandled 500) so main_runtime's existing
        # `except ValueError` branch maps it to a clean 422.
        raise ValueError("knowledge_base_not_indexed") from exc

    try:
        retriever = PGVectorDocumentRetriever(vector_store=store, top_k=top_k)
        # No `query=` kwarg - that's what keeps the retriever on the vector-only path
        # (`_embedding_retrieval`) rather than `_hybrid_retrieval`, regardless of the
        # KB's `retrieval_mode` (already rejected above for 'hybrid' either way).
        result = await asyncio.to_thread(retriever.run, query_embedding=query_embedding, top_k=top_k)
    finally:
        await asyncio.to_thread(store.close)

    return [
        {"content": doc.content, "metadata": doc.metadata, "score": doc.score} for doc in result["documents"]
    ]
