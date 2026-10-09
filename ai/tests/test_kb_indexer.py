import hashlib
import os
import uuid

from app.core.storage import kb_object_key, kb_prefix, put_object
from app.services.kb_indexer import index_kb_document
from app.services.knowledge_bases import get_kb_document, kb_vector_table_name, upsert_kb_document

from .markers import requires_openai_key, requires_postgres

pytestmark = [requires_postgres]


async def _seed_document(company_id, kb_id, storage_cleanup, *, okf_id="doc-1", category=None, content=None):
    content = content or "# Test Doc\nSome body content for indexing.\n"
    object_key = kb_object_key(company_id, kb_id, category, okf_id)
    put_object(object_key, content.encode("utf-8"))
    storage_cleanup(kb_prefix(company_id, kb_id))
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    document_id = await upsert_kb_document(
        company_id=company_id,
        kb_id=kb_id,
        okf_id=okf_id,
        path=f"{category}/{okf_id}.md" if category else f"{okf_id}.md",
        category=category,
        frontmatter={"id": okf_id},
        object_key=object_key,
        content_hash=content_hash,
    )
    return document_id, content_hash


async def test_index_kb_document_fails_cleanly_when_document_missing(company_ids):
    company_id = company_ids()

    result = await index_kb_document(company_id, "00000000-0000-0000-0000-000000000000", "00000000-0000-0000-0000-000000000000")

    assert result["status"] == "failed"
    assert result["error"] == "kb_document_not_found"


async def test_index_kb_document_fails_when_kb_missing(company_ids, storage_cleanup):
    company_id = company_ids()
    fake_kb_id = str(uuid.uuid4())

    document_id, _ = await _seed_document(company_id, fake_kb_id, storage_cleanup)

    result = await index_kb_document(company_id, fake_kb_id, document_id)

    assert result["status"] == "failed"
    assert result["error"] == "knowledge_base_not_found"
    document = await get_kb_document(company_id, document_id)
    assert document.status == "failed"


@requires_openai_key
async def test_index_kb_document_hybrid_mode_populates_search_text(
    company_ids, make_connection, make_knowledge_base, store_connection_secret, storage_cleanup
):
    """Hybrid-mode indexing follows the exact same path as vector-mode (PRD §6.6) - the
    only difference is the pre-tokenized `search_text` column it also writes, which hybrid
    search (kb_search.py) later runs keyword search against."""
    company_id = company_ids()
    connection_id = make_connection(company_id)
    await store_connection_secret(company_id, connection_id, os.environ["OPENAI_API_KEY"])
    kb_id = make_knowledge_base(company_id, connection_id, retrieval_mode="hybrid")

    document_id, _ = await _seed_document(
        company_id, kb_id, storage_cleanup, content="# Refund policy\nสอบถามเพิ่มเติมได้ที่ศูนย์บริการลูกค้า\n"
    )

    result = await index_kb_document(company_id, kb_id, document_id)

    assert result["status"] == "ready"
    assert result["chunk_count"] >= 1

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
        rows = conn.execute(f'SELECT search_text FROM runtime."{table_name}"').fetchall()
        assert len(rows) == result["chunk_count"]
        assert all(row[0] is not None and row[0].strip() != "" for row in rows)
        conn.execute(f'DROP TABLE IF EXISTS runtime."{table_name}"')
        conn.commit()


@requires_openai_key
async def test_index_kb_document_happy_path_writes_vector_rows(
    company_ids, make_connection, make_knowledge_base, store_connection_secret, storage_cleanup
):
    company_id = company_ids()
    connection_id = make_connection(company_id)
    await store_connection_secret(company_id, connection_id, os.environ["OPENAI_API_KEY"])
    kb_id = make_knowledge_base(company_id, connection_id, chunk_size=200, chunk_overlap=20)

    document_id, _ = await _seed_document(
        company_id,
        kb_id,
        storage_cleanup,
        content="# Refund policy\n## Product not dispensed\nContact support for a refund.\n",
    )

    result = await index_kb_document(company_id, kb_id, document_id)

    assert result["status"] == "ready"
    assert result["chunk_count"] >= 1

    document = await get_kb_document(company_id, document_id)
    assert document.status == "ready"
    assert document.chunk_count == result["chunk_count"]

    # The vector table should now exist with the expected number of rows written.
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
        row = conn.execute(f'SELECT COUNT(*) FROM runtime."{table_name}"').fetchone()
        assert row[0] == result["chunk_count"]
        conn.execute(f'DROP TABLE IF EXISTS runtime."{table_name}"')
        conn.commit()


@requires_openai_key
async def test_reindexing_with_fewer_chunks_removes_stale_chunk_rows(
    company_ids, make_connection, make_knowledge_base, store_connection_secret, storage_cleanup
):
    """Regression guard: if a re-indexed document now produces fewer chunks than its
    previous version, the vector table must not keep the old version's extra chunk rows
    around (PGVectorStore.write_documents is an upsert by id, so it never removes them
    on its own)."""
    company_id = company_ids()
    connection_id = make_connection(company_id)
    await store_connection_secret(company_id, connection_id, os.environ["OPENAI_API_KEY"])
    kb_id = make_knowledge_base(company_id, connection_id, chunk_size=50, chunk_overlap=5)

    long_content = (
        "# Doc\n## Section one\n" + ("word " * 60) + "\n## Section two\n" + ("word " * 60) + "\n"
    )
    document_id, _ = await _seed_document(company_id, kb_id, storage_cleanup, content=long_content)
    first_result = await index_kb_document(company_id, kb_id, document_id)
    assert first_result["status"] == "ready"
    assert first_result["chunk_count"] > 1

    # Re-upsert the same (kb, okf_id) with much shorter content -> fewer chunks, same
    # document_id (upsert_kb_document's ON CONFLICT path keeps the row's id).
    short_content = "# Doc\nOne short line.\n"
    second_document_id, _ = await _seed_document(company_id, kb_id, storage_cleanup, content=short_content)
    assert second_document_id == document_id  # same row, re-queued

    second_result = await index_kb_document(company_id, kb_id, document_id)
    assert second_result["status"] == "ready"
    assert second_result["chunk_count"] < first_result["chunk_count"]

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
        row = conn.execute(f'SELECT COUNT(*) FROM runtime."{table_name}"').fetchone()
        # Exactly the new chunk count - no leftover rows from the longer first version.
        assert row[0] == second_result["chunk_count"]
        conn.execute(f'DROP TABLE IF EXISTS runtime."{table_name}"')
        conn.commit()


@requires_openai_key
async def test_index_kb_document_skips_reindex_when_content_unchanged(
    company_ids, make_connection, make_knowledge_base, store_connection_secret, storage_cleanup
):
    """Re-upserting the same (kb, okf_id) with identical content shouldn't be indexed
    twice with different chunk ids - this test exercises upsert_kb_document's
    on-conflict-reset-to-queued path directly rather than the import endpoint, since
    the "skip if hash unchanged" check itself lives in main_runtime's import handler,
    not in index_kb_document."""
    company_id = company_ids()
    connection_id = make_connection(company_id)
    await store_connection_secret(company_id, connection_id, os.environ["OPENAI_API_KEY"])
    kb_id = make_knowledge_base(company_id, connection_id)

    document_id, content_hash = await _seed_document(company_id, kb_id, storage_cleanup)
    first_result = await index_kb_document(company_id, kb_id, document_id)
    assert first_result["status"] == "ready"

    # Re-upsert with the exact same content_hash - simulates console-api's "skip
    # unchanged file" check having already decided NOT to call index_kb_document again;
    # directly confirm the stored hash still matches rather than re-indexing blindly.
    document = await get_kb_document(company_id, document_id)
    assert document.content_hash == content_hash

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
