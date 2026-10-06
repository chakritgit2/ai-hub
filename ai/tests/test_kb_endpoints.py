"""Exercises the actual `/internal/v1/kb/*` FastAPI routes (not just the service
functions directly, which tests/test_kb_indexer.py etc. already cover) - the request/
response model wiring, routing, and auth dependency are what's different here."""
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main_runtime import app as runtime_app
from app.main_runtime import require_internal_auth
from app.services.knowledge_bases import list_kb_documents

from .markers import fake_internal_claims, requires_minio, requires_openai_key, requires_postgres, requires_redis

pytestmark = [requires_postgres, requires_minio, requires_redis]


@pytest.fixture
async def internal_client(company_ids):
    """Same pattern as test_connection_secret_endpoints.py's fixture of the same name -
    an async httpx.AsyncClient wired to require_internal_auth via dependency_overrides,
    sharing this test's event loop with get_company_session-based fixtures."""
    company_id = company_ids()
    runtime_app.dependency_overrides[require_internal_auth] = fake_internal_claims(company_id)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime_app), base_url="http://test") as client:
            yield client, company_id
    finally:
        runtime_app.dependency_overrides.pop(require_internal_auth, None)


async def test_search_route_404s_for_unknown_kb(internal_client):
    client, _company_id = internal_client

    resp = await client.post(
        "/internal/v1/kb/00000000-0000-0000-0000-000000000000/search", json={"query": "anything"}
    )

    assert resp.status_code == 404


async def test_export_route_404s_for_unknown_kb(internal_client):
    client, _company_id = internal_client

    resp = await client.get("/internal/v1/kb/00000000-0000-0000-0000-000000000000/export")

    assert resp.status_code == 404


async def test_list_documents_route_404s_for_unknown_kb(internal_client):
    client, _company_id = internal_client

    resp = await client.get("/internal/v1/kb/00000000-0000-0000-0000-000000000000/documents")

    assert resp.status_code == 404


async def test_list_documents_route_returns_seeded_documents(
    internal_client, make_connection, make_knowledge_base, minio_cleanup
):
    import hashlib

    from app.core.storage import kb_object_key, kb_prefix, put_object
    from app.services.knowledge_bases import upsert_kb_document

    client, company_id = internal_client
    connection_id = make_connection(company_id)
    kb_id = make_knowledge_base(company_id, connection_id)
    minio_cleanup(kb_prefix(company_id, kb_id))

    content = b"# Doc"
    object_key = kb_object_key(company_id, kb_id, None, "doc-1")
    put_object(object_key, content)
    await upsert_kb_document(
        company_id=company_id,
        kb_id=kb_id,
        okf_id="doc-1",
        path="doc-1.md",
        category=None,
        frontmatter={},
        object_key=object_key,
        content_hash=hashlib.sha256(content).hexdigest(),
    )

    resp = await client.get(f"/internal/v1/kb/{kb_id}/documents")

    assert resp.status_code == 200
    documents = resp.json()
    assert len(documents) == 1
    assert documents[0]["path"] == "doc-1.md"
    assert documents[0]["status"] == "queued"


async def test_import_same_filename_in_different_categories_does_not_collide(
    internal_client, make_connection, make_knowledge_base, minio_cleanup
):
    """Regression guard: two files named identically in different zip folders (e.g.
    en/faq.md, th/faq.md), neither with a frontmatter id, must not collide on okf_id -
    the fallback id has to include category, not just the bare filename."""
    client, company_id = internal_client
    connection_id = make_connection(company_id)
    kb_id = make_knowledge_base(company_id, connection_id)

    from app.core.storage import kb_prefix

    minio_cleanup(kb_prefix(company_id, kb_id))

    resp = await client.post(
        f"/internal/v1/kb/{kb_id}/documents",
        json=[
            {"category": "en", "filename": "faq.md", "content": "# FAQ (English)"},
            {"category": "th", "filename": "faq.md", "content": "# FAQ (Thai)"},
        ],
    )

    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 2
    assert results[0]["document_id"] != results[1]["document_id"]
    assert {r["path"] for r in results} == {"en/faq.md", "th/faq.md"}


def test_import_route_requires_internal_token():
    resp = TestClient(runtime_app).post(
        "/internal/v1/kb/00000000-0000-0000-0000-000000000000/documents",
        json=[{"category": None, "filename": "a.md", "content": "# A"}],
        headers={"X-Company-Id": "company-1"},
    )

    assert resp.status_code == 401


@requires_openai_key
async def test_full_import_search_export_delete_round_trip(
    internal_client, make_connection, make_knowledge_base, store_connection_secret, minio_cleanup
):
    import asyncio

    client, company_id = internal_client
    connection_id = make_connection(company_id)
    await store_connection_secret(company_id, connection_id, os.environ["OPENAI_API_KEY"])
    kb_id = make_knowledge_base(company_id, connection_id, chunk_size=200, chunk_overlap=20)

    from app.core.storage import kb_prefix

    minio_cleanup(kb_prefix(company_id, kb_id))

    import_resp = await client.post(
        f"/internal/v1/kb/{kb_id}/documents",
        json=[
            {
                "category": "policies",
                "filename": "refund.md",
                "content": "# Refund policy\nContact support for a refund within 7 days.\n",
            }
        ],
    )
    assert import_resp.status_code == 200
    results = import_resp.json()
    assert len(results) == 1
    assert results[0]["status"] == "queued"
    document_id = results[0]["document_id"]

    # The import route only enqueues the ARQ job - wait for the worker-equivalent
    # processing to land by calling the indexing function directly (no worker process
    # runs in this test suite), same as test_kb_indexer.py does.
    from app.services.kb_indexer import index_kb_document

    indexed = await index_kb_document(company_id, kb_id, document_id)
    assert indexed["status"] == "ready"

    search_resp = await client.post(f"/internal/v1/kb/{kb_id}/search", json={"query": "How do I get a refund?"})
    assert search_resp.status_code == 200
    search_results = search_resp.json()["results"]
    assert len(search_results) >= 1

    export_resp = await client.get(f"/internal/v1/kb/{kb_id}/export")
    assert export_resp.status_code == 200
    assert export_resp.headers["content-type"] == "application/zip"

    import zipfile
    from io import BytesIO

    with zipfile.ZipFile(BytesIO(export_resp.content)) as archive:
        assert "policies/refund.md" in archive.namelist()

    delete_resp = await client.delete(f"/internal/v1/kb/{kb_id}")
    assert delete_resp.status_code == 204

    remaining = await list_kb_documents(company_id, kb_id)
    assert remaining == []
