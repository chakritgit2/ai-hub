"""Thai tokenizer stub (PRD §6.6).

Postgres full-text search cannot segment Thai (no spaces between words), so
KB indexing stores a pre-tokenized `search_text` field using PyThaiNLP and
the keyword side of hybrid retrieval uses the `simple` text search
configuration over that pre-tokenized text.

Note: the PyThaiNLP dependency itself is deferred to phase 2 (KB indexing is
out of scope for this phase-1 skeleton) - this stub only fixes the call
signature so callers can be wired up later without an API change.
"""


def tokenize(text: str) -> list[str]:
    raise NotImplementedError("tokenize: PyThaiNLP-based Thai tokenization deferred to phase 2")
