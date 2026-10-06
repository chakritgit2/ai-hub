"""OKF (Open Knowledge Format) parser (PRD §6.6).

OKF is one Markdown file per topic with YAML frontmatter metadata (id, title, tags,
lang, version, updated_at, source, ...). The frontmatter reader maps fields from
per-KB configuration (`knowledge_bases.okf_field_map`) so the schema can change
without code changes; `id` defaults to the file path and `title` to the first H1 when
missing from frontmatter (PRD §12).
"""
import re

import yaml

_FRONTMATTER_PATTERN = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.DOTALL)
_FIRST_H1_PATTERN = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def parse_okf(markdown: str) -> dict:
    """Parses an OKF Markdown document into `{"frontmatter": dict, "body": str}`.

    A document with no `---`-delimited frontmatter block at all is valid OKF too
    (PRD §12's "OKF file without frontmatter" edge case) - it just yields an empty
    frontmatter dict and the whole document as body.
    """
    match = _FRONTMATTER_PATTERN.match(markdown)
    if not match:
        return {"frontmatter": {}, "body": markdown}

    raw_frontmatter, body = match.group(1), match.group(2)
    frontmatter = yaml.safe_load(raw_frontmatter)
    if not isinstance(frontmatter, dict):
        # A frontmatter block that parses to a scalar/list (e.g. "---\n- a\n---") isn't
        # a mapping of fields - treat it the same as "no frontmatter" rather than
        # crashing on frontmatter["id"] below.
        frontmatter = {}

    return {"frontmatter": frontmatter, "body": body}


def resolve_okf_metadata(frontmatter: dict, field_map: dict, fallback_path: str, body: str) -> dict:
    """Applies `okf_field_map` (per-KB JSONB config, PRD §8.2) to remap frontmatter
    field names, then fills in the PRD §12 defaults for anything still missing:
    `id` <- `fallback_path`, `title` <- the first `# ...` heading in `body`.

    `field_map` maps our canonical field name to the frontmatter's actual key for
    that field (e.g. `{"id": "doc_id"}` reads `frontmatter["doc_id"]` as `id`) -
    canonical names with no entry in `field_map` are read under their own name.
    """
    remapped: dict = {}
    for canonical_name, value in frontmatter.items():
        remapped[canonical_name] = value
    for canonical_name, source_key in field_map.items():
        if source_key in frontmatter:
            remapped[canonical_name] = frontmatter[source_key]

    if not remapped.get("id"):
        remapped["id"] = fallback_path

    if not remapped.get("title"):
        heading_match = _FIRST_H1_PATTERN.search(body)
        remapped["title"] = heading_match.group(1) if heading_match else fallback_path

    return remapped
