"""Local/PVC-mounted filesystem object storage (PRD §6.6/§7.7).

OKF files live under `{KB_STORAGE_ROOT}/kb/{company_id}/{kb_id}/{category}/{okf_id}.md` -
per-company/per-KB prefixes, paths always built from `company_id`/`kb_id` resolved
server-side (PRD §7.7), never from request input directly. Previously MinIO (an object
store); switched to plain files since nothing here needs more than "put/get/delete a blob
by key" and MinIO was an extra licensed service for no real benefit at this scale.

Plain `pathlib`/`os` calls are synchronous - same discipline `PGVectorStore` (also
sync/psycopg) already needs: async callers (kb_indexer/kb_search/kb_export, all
FastAPI/ARQ) must wrap calls with `asyncio.to_thread`.
"""
import shutil
from pathlib import Path

from app.core.config import get_settings


def _resolve(key: str) -> Path:
    """Joins `key` onto `KB_STORAGE_ROOT` and checks the result still lands inside it -
    `key` is always built by `kb_object_key`/`kb_prefix` from server-resolved
    `company_id`/`kb_id` (PRD §7.7), never raw request input, but this is a cheap
    defense-in-depth backstop against a `key` that somehow contained `..` anyway."""
    root = Path(get_settings().KB_STORAGE_ROOT).resolve()
    path = (root / key).resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"object key escapes storage root: {key!r}")
    return path


def put_object(key: str, data: bytes, content_type: str = "text/markdown") -> None:
    """`content_type` is accepted for interface compatibility with the previous
    MinIO-backed version but unused - a plain file has no separate content-type
    metadata slot, and every caller already knows the extension (`.md`)."""
    path = _resolve(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def get_object(key: str) -> bytes:
    path = _resolve(key)
    try:
        return path.read_bytes()
    except FileNotFoundError:
        raise FileNotFoundError(f"no object at key {key!r}") from None


def delete_object(key: str) -> None:
    _resolve(key).unlink(missing_ok=True)


def delete_prefix(prefix: str) -> None:
    """Deletes every object under `prefix` - used when a KB is deleted
    (`kb/{company_id}/{kb_id}/`)."""
    shutil.rmtree(_resolve(prefix), ignore_errors=True)


def kb_object_key(company_id: str, kb_id: str, category: str | None, okf_id: str) -> str:
    """Path convention: kb/{company_id}/{kb_id}/{category}/{okf_id}.md - category is
    omitted from the path entirely when absent (root of the KB), not an empty segment."""
    prefix = f"kb/{company_id}/{kb_id}"
    if category:
        return f"{prefix}/{category}/{okf_id}.md"
    return f"{prefix}/{okf_id}.md"


def kb_prefix(company_id: str, kb_id: str) -> str:
    return f"kb/{company_id}/{kb_id}/"
