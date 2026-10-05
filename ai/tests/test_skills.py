import uuid

import psycopg
import pytest
from dynamiq.skills.types import SkillRegistryError

from app.core.config import get_settings
from app.integrations.skill_registry import ConsoleSkillRegistry, get_console_skill_registry
from app.services.skills import PublishedSkill, list_published_skills

from .markers import requires_postgres


def _skill(name: str, content: str, description: str | None = None) -> PublishedSkill:
    return PublishedSkill(name=name, description=description, content=content)


def test_get_skills_metadata_reflects_preloaded_skills():
    registry = ConsoleSkillRegistry(
        skills=[_skill("refund-policy", "# Refund Policy", description="How refunds work")]
    )

    metadata = registry.get_skills_metadata()

    assert len(metadata) == 1
    assert metadata[0].name == "refund-policy"
    assert metadata[0].description == "How refunds work"


def test_get_skill_instructions_returns_the_right_content():
    registry = ConsoleSkillRegistry(
        skills=[_skill("refund-policy", "# Refund Policy\n\nFull refund within 7 days.")]
    )

    instructions = registry.get_skill_instructions("refund-policy")

    assert instructions.name == "refund-policy"
    assert instructions.instructions == "# Refund Policy\n\nFull refund within 7 days."


def test_get_skill_instructions_raises_for_unknown_name():
    registry = ConsoleSkillRegistry(skills=[_skill("refund-policy", "# Refund Policy")])

    with pytest.raises(SkillRegistryError):
        registry.get_skill_instructions("no-such-skill")


@requires_postgres
async def test_list_published_skills_excludes_draft_only_skills(company_ids, make_skill):
    company_id = company_ids()
    make_skill(company_id, name="draft-skill", is_published=False)

    skills = await list_published_skills(company_id)

    assert skills == []


@requires_postgres
async def test_list_published_skills_returns_only_the_calling_companys_rows(company_ids, make_skill):
    company_id = company_ids()
    other_company_id = str(uuid.uuid4())
    make_skill(other_company_id, name="other-companys-skill")

    skills = await list_published_skills(company_id)

    assert skills == []
    assert company_id != other_company_id


@requires_postgres
async def test_list_published_skills_returns_the_latest_published_version(company_ids, make_skill):
    company_id = company_ids()
    skill_id = make_skill(company_id, name="refund-policy", content="v1 content", version_no=1, is_published=True)
    # A second, later version of the same skill, inserted directly via the same console_app
    # role the fixture uses - reuses skill_id rather than calling make_skill again (which would
    # create a brand new skill row).
    settings = get_settings()
    v2_id = str(uuid.uuid4())

    def _connect() -> "psycopg.Connection":
        return psycopg.connect(
            host=settings.MEMORY_DB_HOST,
            port=settings.MEMORY_DB_PORT,
            dbname=settings.MEMORY_DB_NAME,
            user="console_app",
            password="changeme_local_dev_only",
        )

    with _connect() as conn:
        conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
        conn.execute(
            "INSERT INTO console.skill_versions "
            "(id, company_id, skill_id, version_no, content, content_hash, is_published) "
            "VALUES (%s, %s, %s, 2, %s, %s, true)",
            (v2_id, company_id, skill_id, "v2 content", "placeholder-hash-2"),
        )
        conn.commit()

    try:
        skills = await list_published_skills(company_id)

        assert len(skills) == 1
        assert skills[0].content == "v2 content"
    finally:
        # make_skill's own teardown only deletes the version row it created (v1) - without
        # this, deleting console.skills afterward would hit the skill_versions.skill_id FK
        # still pointing at this manually-inserted v2 row.
        with _connect() as conn:
            conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
            conn.execute("DELETE FROM console.skill_versions WHERE id = %s", (v2_id,))
            conn.commit()


@requires_postgres
async def test_get_console_skill_registry_round_trips_seeded_data(company_ids, make_skill):
    company_id = company_ids()
    make_skill(company_id, name="refund-policy", content="# Refund Policy", description="refunds")

    registry = await get_console_skill_registry(company_id)

    metadata = registry.get_skills_metadata()
    assert [m.name for m in metadata] == ["refund-policy"]
    instructions = registry.get_skill_instructions("refund-policy")
    assert instructions.instructions == "# Refund Policy"
