"""Knowledge base indexing service stub (PRD §6.6, phase 2).

Real implementation splits OKF body text with `MarkdownHeaderSplitter`,
embeds chunks with the KB owner's connection, and stores them in a per-KB
pgvector table (`runtime.kbv_{company_id}_{kb_id}`) via:

    from dynamiq.storages.vector.pgvector.pgvector import PGVectorStore

The retriever resolves the table only from the KB owned by the company in
context, never from the request/spec.
"""


async def index_kb_document(company_id: str, kb_id: str, document_id: str) -> dict:
    raise NotImplementedError("index_kb_document: PGVectorStore-backed indexing not yet implemented")
