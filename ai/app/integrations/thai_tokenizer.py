"""Thai tokenizer (PRD §6.6).

Postgres full-text search cannot segment Thai (no spaces between words), so KB indexing
stores a pre-tokenized `search_text` field (space-joined tokens) and the keyword side of
hybrid retrieval runs `to_tsvector('simple', ...)` over that pre-tokenized text instead of
relying on Postgres's own (Thai-unaware) text search configurations. Applied uniformly
regardless of detected language - sidesteps needing per-language detection at all, and
`newmm` tokenizes non-Thai text reasonably too (see app.services.kb_hybrid).
"""
from pythainlp.tokenize import word_tokenize


def tokenize(text: str) -> list[str]:
    return [token for token in word_tokenize(text, engine="newmm") if token.strip()]
