"""MinIO object storage client singleton (PRD §6.6/§7.7).

OKF files live in MinIO under `kb/{company_id}/{kb_id}/{category}/{okf_id}.md` - one
bucket, per-company/per-KB prefixes, paths always built from `company_id`/`kb_id`
resolved server-side (PRD §7.7), never from request input directly.

The `minio` SDK is synchronous (no native async client) - every call here blocks, so
async callers (kb_indexer/kb_search/kb_export, all FastAPI/ARQ) must wrap calls with
`asyncio.to_thread`, same discipline `PGVectorStore` (also sync/psycopg) already needs.
"""
from functools import lru_cache
from typing import BinaryIO

from minio import Minio
from minio.deleteobjects import DeleteObject

from app.core.config import get_settings


@lru_cache
def get_minio_client() -> Minio:
    settings = get_settings()
    client = Minio(
        settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        secure=settings.MINIO_SECURE,
    )
    _ensure_bucket_exists(client, settings.MINIO_BUCKET)
    return client


def _ensure_bucket_exists(client: Minio, bucket: str) -> None:
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)


def put_object(key: str, data: bytes, content_type: str = "text/markdown") -> None:
    import io

    settings = get_settings()
    client = get_minio_client()
    stream: BinaryIO = io.BytesIO(data)
    client.put_object(settings.MINIO_BUCKET, key, stream, length=len(data), content_type=content_type)


def get_object(key: str) -> bytes:
    settings = get_settings()
    client = get_minio_client()
    response = client.get_object(settings.MINIO_BUCKET, key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def delete_object(key: str) -> None:
    settings = get_settings()
    client = get_minio_client()
    client.remove_object(settings.MINIO_BUCKET, key)


def delete_prefix(prefix: str) -> None:
    """Deletes every object under `prefix` - used when a KB is deleted
    (`kb/{company_id}/{kb_id}/`)."""
    settings = get_settings()
    client = get_minio_client()
    objects = client.list_objects(settings.MINIO_BUCKET, prefix=prefix, recursive=True)
    delete_object_list = (DeleteObject(obj.object_name) for obj in objects)
    for error in client.remove_objects(settings.MINIO_BUCKET, delete_object_list):
        raise RuntimeError(f"failed to delete {error.name}: {error.message}")


def kb_object_key(company_id: str, kb_id: str, category: str | None, okf_id: str) -> str:
    """Path convention: kb/{company_id}/{kb_id}/{category}/{okf_id}.md - category is
    omitted from the path entirely when absent (root of the KB), not an empty segment."""
    prefix = f"kb/{company_id}/{kb_id}"
    if category:
        return f"{prefix}/{category}/{okf_id}.md"
    return f"{prefix}/{okf_id}.md"


def kb_prefix(company_id: str, kb_id: str) -> str:
    return f"kb/{company_id}/{kb_id}/"
