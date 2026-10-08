import os

import pytest

from app.core.storage import kb_object_key, kb_prefix, put_object
from app.services.kb_indexer import index_kb_document
from app.services.kb_search import search_knowledge_base
from app.services.knowledge_bases import kb_vector_table_name, upsert_kb_document

from .markers import requires_minio, requires_openai_key, requires_postgres

pytestmark = [requires_postgres, requires_minio]


async def test_search_knowledge_base_raises_for_missing_kb(company_ids):
    company_id = company_ids()

    with pytest.raises(ValueError, match="knowledge_base_not_found"):
        await search_knowledge_base(company_id, "00000000-0000-0000-0000-000000000000", "anything")


@requires_openai_key
async def test_hybrid_search_finds_document_by_unspaced_thai_term(
    company_ids, make_connection, make_knowledge_base, store_connection_secret, minio_cleanup
):
    """PRD §13's own hybrid-search test scenario: a product code / Thai term with no
    spaces (Postgres's own text search can't segment it) must still surface the right
    document in the top 3, alongside unrelated decoy documents."""
    import hashlib

    company_id = company_ids()
    connection_id = make_connection(company_id)
    await store_connection_secret(company_id, connection_id, os.environ["OPENAI_API_KEY"])
    kb_id = make_knowledge_base(company_id, connection_id, retrieval_mode="hybrid")

    documents = {
        "target": "# ตู้ขายสินค้ารุ่น VM-2026X\nคู่มือการใช้งานตู้ขายสินค้าอัตโนมัติรุ่น VM-2026X สำหรับร้านค้า\n",
        "decoy-1": "# Office supplies\nStaplers, paper clips, and sticky notes are kept in the storage closet.\n",
        "decoy-2": "# Parking policy\nVisitor parking is available in lot B from 9am to 6pm on weekdays.\n",
    }

    for okf_id, content in documents.items():
        object_key = kb_object_key(company_id, kb_id, None, okf_id)
        put_object(object_key, content.encode("utf-8"))
        minio_cleanup(kb_prefix(company_id, kb_id))
        document_id = await upsert_kb_document(
            company_id=company_id,
            kb_id=kb_id,
            okf_id=okf_id,
            path=f"{okf_id}.md",
            category=None,
            frontmatter={"id": okf_id},
            object_key=object_key,
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )
        indexed = await index_kb_document(company_id, kb_id, document_id)
        assert indexed["status"] == "ready"

    # "VM-2026X" has no spaces around it in the Thai sentence it's embedded in - exactly
    # the case Postgres's own (Thai-unaware) text search can't segment on its own.
    results = await search_knowledge_base(company_id, kb_id, "VM-2026X", top_k=3)

    assert any("VM-2026X" in result["content"] for result in results)

    import psycopg

    from app.core.config import get_settings

    settings = get_settings()
    table_name = kb_vector_table_name(company_id, kb_id)
    with psycopg.connect(
        host=settings.MEMORY_DB_HOST,
        port=settings.MEMORY_DB_PORT,
        dbname=settings.MEMORY_DB_NAME,
        user=settings.MEMORY_DB_USER,
        password=settings.MEMORY_DB_PASSWORD,
    ) as conn:
        conn.execute(f'DROP TABLE IF EXISTS runtime."{table_name}"')
        conn.commit()


@requires_openai_key
async def test_search_knowledge_base_before_any_document_indexed_raises_value_error(
    company_ids, make_connection, make_knowledge_base, store_connection_secret
):
    """Regression guard: a brand-new KB with no document indexed yet has no per-KB
    pgvector table at all - PGVectorStore's constructor (create_if_not_exist=False)
    raises VectorStoreException in that case, which must come out as a clean ValueError
    (-> 422), not an unhandled 500. Needs a real key since embedding the query happens
    before the table-existence check."""
    company_id = company_ids()
    connection_id = make_connection(company_id)
    await store_connection_secret(company_id, connection_id, os.environ["OPENAI_API_KEY"])
    kb_id = make_knowledge_base(company_id, connection_id)

    with pytest.raises(ValueError, match="knowledge_base_not_indexed"):
        await search_knowledge_base(company_id, kb_id, "anything")


@requires_openai_key
async def test_search_knowledge_base_returns_relevant_chunk(
    company_ids, make_connection, make_knowledge_base, store_connection_secret, minio_cleanup
):
    company_id = company_ids()
    connection_id = make_connection(company_id)
    await store_connection_secret(company_id, connection_id, os.environ["OPENAI_API_KEY"])
    kb_id = make_knowledge_base(company_id, connection_id)

    content = "# Refund policy\n## Product not dispensed\nContact support for a full refund within 7 days.\n"
    okf_id = "refund-doc"
    object_key = kb_object_key(company_id, kb_id, None, okf_id)
    put_object(object_key, content.encode("utf-8"))
    minio_cleanup(kb_prefix(company_id, kb_id))

    import hashlib

    document_id = await upsert_kb_document(
        company_id=company_id,
        kb_id=kb_id,
        okf_id=okf_id,
        path=f"{okf_id}.md",
        category=None,
        frontmatter={"id": okf_id},
        object_key=object_key,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )

    indexed = await index_kb_document(company_id, kb_id, document_id)
    assert indexed["status"] == "ready"

    results = await search_knowledge_base(company_id, kb_id, "How do I get a refund?", top_k=3)

    assert len(results) >= 1
    assert "refund" in results[0]["content"].lower()
    assert results[0]["score"] is not None

    import psycopg

    from app.core.config import get_settings

    settings = get_settings()
    table_name = kb_vector_table_name(company_id, kb_id)
    with psycopg.connect(
        host=settings.MEMORY_DB_HOST,
        port=settings.MEMORY_DB_PORT,
        dbname=settings.MEMORY_DB_NAME,
        user=settings.MEMORY_DB_USER,
        password=settings.MEMORY_DB_PASSWORD,
    ) as conn:
        conn.execute(f'DROP TABLE IF EXISTS runtime."{table_name}"')
        conn.commit()
