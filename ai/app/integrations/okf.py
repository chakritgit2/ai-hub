"""OKF (Open Knowledge Format) parser stub (PRD §6.6).

OKF is one Markdown file per topic with YAML frontmatter metadata (id,
title, tags, lang, version, updated_at, source, ...). The frontmatter reader
is expected to map fields from configuration (`knowledge_bases.okf_field_map`)
so the schema can change without code changes; `id` defaults to the file
path and `title` to the first H1 when missing from frontmatter.
"""


def parse_okf(markdown: str) -> dict:
    """Parse an OKF Markdown document into `{frontmatter: dict, body: str}`."""
    raise NotImplementedError("parse_okf: OKF frontmatter parsing not yet implemented")
