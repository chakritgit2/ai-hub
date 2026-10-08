"""Hybrid (vector + keyword) retrieval support for Knowledge Bases (PRD §6.6).

The installed `dynamiq==0.65.0`'s `PGVectorStore`/`PGVectorDocumentRetriever` ties its own
built-in hybrid retrieval's tsvector source to the same `content_key` column it returns as
document content - there is no way to point keyword search at a separate pre-tokenized
`search_text` column while still returning the original, readable `content`. So this module
doesn't call Dynamiq's `_hybrid_retrieval` at all: it manages its own `search_text` column
and keyword query alongside Dynamiq's existing (unchanged) vector-only retrieval, and
`fuse_rankings` combines the two result sets ourselves.

`table_name` throughout is always `app.services.knowledge_bases.kb_vector_table_name`'s
output - validated there against a strict `kbv_<hex>_<hex>` pattern, so it's safe to
interpolate directly into SQL as an identifier (same convention already used by
`kb_indexer.py`'s own tests).
"""
import psycopg

from app.core.config import get_settings


def _connect() -> psycopg.Connection:
    settings = get_settings()
    return psycopg.connect(
        host=settings.MEMORY_DB_HOST,
        port=settings.MEMORY_DB_PORT,
        dbname=settings.MEMORY_DB_NAME,
        user=settings.MEMORY_DB_USER,
        password=settings.MEMORY_DB_PASSWORD,
    )


def ensure_search_text_column(table_name: str) -> None:
    with _connect() as conn:
        conn.execute(f'ALTER TABLE runtime."{table_name}" ADD COLUMN IF NOT EXISTS search_text text')
        conn.execute(
            f'CREATE INDEX IF NOT EXISTS "{table_name}_search_text_gin_idx" ON runtime."{table_name}" '
            "USING GIN (to_tsvector('simple', coalesce(search_text, '')))"
        )
        conn.commit()


def write_search_text_batch(table_name: str, id_to_text: dict[str, str]) -> None:
    if not id_to_text:
        return
    with _connect() as conn:
        conn.cursor().executemany(
            f'UPDATE runtime."{table_name}" SET search_text = %s WHERE id = %s',
            [(text, chunk_id) for chunk_id, text in id_to_text.items()],
        )
        conn.commit()


def keyword_search(table_name: str, tokenized_query: str, limit: int) -> list[str]:
    """Returns chunk ids ranked by `ts_rank` against the pre-tokenized `search_text`
    column, highest rank first. Empty when `tokenized_query` has no tokens (an all-stopword
    or empty search string) - `plainto_tsquery` on an empty string matches nothing useful."""
    if not tokenized_query.strip():
        return []
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id FROM runtime."{table_name}"
            WHERE to_tsvector('simple', coalesce(search_text, '')) @@ plainto_tsquery('simple', %s)
            ORDER BY ts_rank(to_tsvector('simple', coalesce(search_text, '')), plainto_tsquery('simple', %s)) DESC
            LIMIT %s
            """,
            (tokenized_query, tokenized_query, limit),
        ).fetchall()
    return [row[0] for row in rows]


def fuse_rankings(vector_ids: list[str], keyword_ids: list[str], alpha: float, k: int = 60) -> list[str]:
    """Weighted Reciprocal Rank Fusion of two ranked id lists (pure function, no DB) -
    `alpha` weights the vector list's contribution vs. the keyword list's (PRD §6.6:
    "weighted by alpha"). `k` matches Dynamiq's own `keyword_rank_constant` default (`60`)
    for familiarity, though this isn't Dynamiq's implementation. An id present in only one
    list still surfaces, scored from that list alone."""
    vector_rank = {chunk_id: rank for rank, chunk_id in enumerate(vector_ids)}
    keyword_rank = {chunk_id: rank for rank, chunk_id in enumerate(keyword_ids)}

    scores: dict[str, float] = {}
    for chunk_id in set(vector_ids) | set(keyword_ids):
        score = 0.0
        if chunk_id in vector_rank:
            score += alpha * (1.0 / (k + vector_rank[chunk_id]))
        if chunk_id in keyword_rank:
            score += (1.0 - alpha) * (1.0 / (k + keyword_rank[chunk_id]))
        scores[chunk_id] = score

    return sorted(scores, key=lambda chunk_id: scores[chunk_id], reverse=True)
