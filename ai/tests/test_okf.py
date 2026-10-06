from app.integrations.okf import parse_okf, resolve_okf_metadata


def test_parse_okf_extracts_frontmatter_and_body():
    markdown = (
        "---\n"
        "id: refund-policy\n"
        "title: Vending refund policy\n"
        "tags: [refund, vending]\n"
        "---\n"
        "# Refund policy\n"
        "Body text.\n"
    )

    result = parse_okf(markdown)

    assert result["frontmatter"] == {
        "id": "refund-policy",
        "title": "Vending refund policy",
        "tags": ["refund", "vending"],
    }
    assert result["body"] == "# Refund policy\nBody text.\n"


def test_parse_okf_with_no_frontmatter_block():
    markdown = "# Just a heading\nSome text\n"

    result = parse_okf(markdown)

    assert result["frontmatter"] == {}
    assert result["body"] == markdown


def test_parse_okf_with_non_mapping_frontmatter_is_treated_as_empty():
    """A `---`-delimited block that parses to a list/scalar (not a mapping) isn't valid
    OKF frontmatter - PRD §12 treats a missing/malformed frontmatter the same way."""
    markdown = "---\n- a\n- b\n---\nBody\n"

    result = parse_okf(markdown)

    assert result["frontmatter"] == {}
    assert result["body"] == "Body\n"


def test_resolve_okf_metadata_uses_frontmatter_values_directly():
    metadata = resolve_okf_metadata(
        {"id": "refund-policy", "title": "Vending refund policy"}, {}, "fallback/path.md", "body"
    )

    assert metadata["id"] == "refund-policy"
    assert metadata["title"] == "Vending refund policy"


def test_resolve_okf_metadata_defaults_id_to_fallback_path_when_missing():
    metadata = resolve_okf_metadata({}, {}, "policies/refund.md", "# Refund policy\nBody")

    assert metadata["id"] == "policies/refund.md"
    assert metadata["title"] == "Refund policy"


def test_resolve_okf_metadata_defaults_title_to_fallback_when_no_heading():
    metadata = resolve_okf_metadata({}, {}, "policies/refund.md", "no heading here")

    assert metadata["title"] == "policies/refund.md"


def test_resolve_okf_metadata_applies_field_map():
    """`okf_field_map` (per-KB config) remaps a canonical field to a differently-named
    frontmatter key (PRD §6.6 Open Question 10's draft frontmatter reader)."""
    metadata = resolve_okf_metadata(
        {"doc_id": "custom-id", "doc_title": "Custom Title"},
        {"id": "doc_id", "title": "doc_title"},
        "fallback.md",
        "body",
    )

    assert metadata["id"] == "custom-id"
    assert metadata["title"] == "Custom Title"
