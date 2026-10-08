"""Knowledge base search/retrieval (PRD §6.6).

Vector-only retrieval goes straight through Dynamiq's `PGVectorDocumentRetriever`, unchanged.
Hybrid retrieval does **not** call Dynamiq's own `_hybrid_retrieval` — see
`app.services.kb_hybrid`'s module docstring for why (it ties keyword search to the same
column it returns as content, with no way to point it at a separate pre-tokenized
`search_text` field). Instead this runs the same vector retrieval plus a separate keyword
search against the `search_text` column `kb_indexer.py` populates, then fuses the two ranked
lists itself.
"""
import asyncio

from dynamiq.components.retrievers.pgvector import PGVectorDocumentRetriever
from dynamiq.connections.connections import PostgreSQL as PostgreSQLConnection
from dynamiq.storages.vector.exceptions import VectorStoreException
from dynamiq.storages.vector.pgvector.pgvector import PGVectorIndexMethod, PGVectorStore

from app.core.config import get_settings
from app.integrations.dynamiq_adapter import build_embedder
from app.integrations.thai_tokenizer import tokenize
from app.services.connection_secrets import decrypt_connection_secret
from app.services.connections import resolve_connection
from app.services.kb_hybrid import fuse_rankings, keyword_search
from app.services.knowledge_bases import kb_vector_table_name, resolve_knowledge_base

_EMBEDDING_DIMENSION = 1536  # must match kb_indexer.py's indexing dimension
_SUBQUERY_MULTIPLIER = 4  # matches Dynamiq's own top_k_subquery_multiplier default


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


def _hybrid_search(
    store: PGVectorStore, table_name: str, query: str, query_embedding: list[float], top_k: int, alpha: float
) -> list[dict]:
    """Sync (run via asyncio.to_thread like the rest of this module's store access) -
    vector + keyword retrieval, fused by `app.services.kb_hybrid.fuse_rankings`. Scores are
    the fused result's own ranking, not comparable to vector-only's cosine-distance score -
    returned as `None` rather than a misleadingly-shaped number."""
    subquery_top_k = top_k * _SUBQUERY_MULTIPLIER

    retriever = PGVectorDocumentRetriever(vector_store=store, top_k=subquery_top_k)
    vector_result = retriever.run(query_embedding=query_embedding, top_k=subquery_top_k)
    vector_ids = [doc.id for doc in vector_result["documents"]]

    tokenized_query = " ".join(tokenize(query))
    keyword_ids = keyword_search(table_name, tokenized_query, subquery_top_k)

    fused_ids = fuse_rankings(vector_ids, keyword_ids, alpha=alpha)[:top_k]
    documents_by_id = {doc.id: doc for doc in store.get_documents_by_id(fused_ids)}
    return [
        {"content": documents_by_id[doc_id].content, "metadata": documents_by_id[doc_id].metadata, "score": None}
        for doc_id in fused_ids
        if doc_id in documents_by_id
    ]


async def search_knowledge_base(company_id: str, kb_id: str, query: str, top_k: int = 5) -> list[dict]:
    kb = await resolve_knowledge_base(company_id, kb_id)
    if kb is None:
        raise ValueError("knowledge_base_not_found")

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
        if kb.retrieval_mode == "hybrid":
            return await asyncio.to_thread(_hybrid_search, store, table_name, query, query_embedding, top_k, kb.alpha)

        retriever = PGVectorDocumentRetriever(vector_store=store, top_k=top_k)
        # No `query=` kwarg - that's what keeps the retriever on the vector-only path
        # (`_embedding_retrieval`) rather than `_hybrid_retrieval`.
        result = await asyncio.to_thread(retriever.run, query_embedding=query_embedding, top_k=top_k)
    finally:
        await asyncio.to_thread(store.close)

    return [
        {"content": doc.content, "metadata": doc.metadata, "score": doc.score} for doc in result["documents"]
    ]
