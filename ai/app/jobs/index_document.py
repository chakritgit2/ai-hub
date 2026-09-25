"""ARQ job: index a single KB document (PRD §6.6, phase 2)."""
from typing import Any

from app.services.kb_indexer import index_kb_document


async def index_document(ctx: dict[str, Any], company_id: str, kb_id: str, document_id: str) -> dict:
    return await index_kb_document(company_id, kb_id, document_id)
