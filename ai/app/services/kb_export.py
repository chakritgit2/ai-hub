"""Knowledge base `.zip` export (PRD §6.6: "download a KB as a .zip of .md files, for
Git or other systems")."""
import asyncio
import zipfile
from io import BytesIO

from app.core.storage import get_object
from app.services.knowledge_bases import list_kb_documents


async def export_knowledge_base_zip(company_id: str, kb_id: str) -> bytes:
    documents = await list_kb_documents(company_id, kb_id)

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for document in documents:
            content = await asyncio.to_thread(get_object, document.object_key)
            # `path` (category/filename, set at import time) already carries the real
            # filename with its actual extension - rebuilding "{okf_id}.md" here instead
            # double-extensions whenever okf_id falls back to the filename itself
            # (resolve_okf_metadata's `id` <- fallback_path, which IS the filename when
            # there's no frontmatter id), e.g. "test-doc.md" -> "test-doc.md.md".
            archive.writestr(document.path, content)

    return buffer.getvalue()
