import hashlib
import zipfile
from io import BytesIO

from app.core.storage import kb_object_key, kb_prefix, put_object
from app.services.kb_export import export_knowledge_base_zip
from app.services.knowledge_bases import upsert_kb_document

from .markers import requires_postgres

pytestmark = [requires_postgres]


async def test_export_knowledge_base_zip_includes_all_documents_with_categories(
    company_ids, make_connection, make_knowledge_base, storage_cleanup
):
    company_id = company_ids()
    connection_id = make_connection(company_id)
    kb_id = make_knowledge_base(company_id, connection_id)
    storage_cleanup(kb_prefix(company_id, kb_id))

    root_content = b"# Root doc"
    nested_content = b"# Refund policy"

    root_key = kb_object_key(company_id, kb_id, None, "root-doc")
    put_object(root_key, root_content)
    await upsert_kb_document(
        company_id=company_id,
        kb_id=kb_id,
        okf_id="root-doc",
        path="root-doc.md",
        category=None,
        frontmatter={},
        object_key=root_key,
        content_hash=hashlib.sha256(root_content).hexdigest(),
    )

    nested_key = kb_object_key(company_id, kb_id, "policies", "refund")
    put_object(nested_key, nested_content)
    await upsert_kb_document(
        company_id=company_id,
        kb_id=kb_id,
        okf_id="refund",
        path="policies/refund.md",
        category="policies",
        frontmatter={},
        object_key=nested_key,
        content_hash=hashlib.sha256(nested_content).hexdigest(),
    )

    zip_bytes = await export_knowledge_base_zip(company_id, kb_id)

    with zipfile.ZipFile(BytesIO(zip_bytes)) as archive:
        names = set(archive.namelist())
        assert "root-doc.md" in names
        assert "policies/refund.md" in names
        assert archive.read("root-doc.md") == root_content
        assert archive.read("policies/refund.md") == nested_content


async def test_export_knowledge_base_zip_uses_path_not_okf_id_plus_extension(
    company_ids, make_connection, make_knowledge_base, storage_cleanup
):
    """Regression guard: when a file has no frontmatter `id`, resolve_okf_metadata falls
    back to the filename itself (e.g. "test-doc.md") as okf_id - rebuilding the archive
    entry name as f"{okf_id}.md" in that case would double the extension
    ("test-doc.md.md"). export_knowledge_base_zip must use the stored `path` directly."""
    company_id = company_ids()
    connection_id = make_connection(company_id)
    kb_id = make_knowledge_base(company_id, connection_id)
    storage_cleanup(kb_prefix(company_id, kb_id))

    content = b"# No frontmatter id here"
    object_key = kb_object_key(company_id, kb_id, None, "test-doc.md")
    put_object(object_key, content)
    await upsert_kb_document(
        company_id=company_id,
        kb_id=kb_id,
        okf_id="test-doc.md",  # falls back to the filename, as resolve_okf_metadata does
        path="test-doc.md",
        category=None,
        frontmatter={},
        object_key=object_key,
        content_hash=hashlib.sha256(content).hexdigest(),
    )

    zip_bytes = await export_knowledge_base_zip(company_id, kb_id)

    with zipfile.ZipFile(BytesIO(zip_bytes)) as archive:
        names = set(archive.namelist())
        assert "test-doc.md" in names
        assert "test-doc.md.md" not in names


async def test_export_knowledge_base_zip_is_empty_for_kb_with_no_documents(
    company_ids, make_connection, make_knowledge_base
):
    company_id = company_ids()
    connection_id = make_connection(company_id)
    kb_id = make_knowledge_base(company_id, connection_id)

    zip_bytes = await export_knowledge_base_zip(company_id, kb_id)

    with zipfile.ZipFile(BytesIO(zip_bytes)) as archive:
        assert archive.namelist() == []
