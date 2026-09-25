"""Console-owned skill registry (PRD §6.6a).

Skills are managed on the Skills page per company (Markdown `SKILL.md` +
supporting files, versioned/published) and stored in Postgres/MinIO.
`ConsoleSkillRegistry` subclasses Dynamiq's `BaseSkillRegistry` so agents can
load skills on demand (`SkillsTool`: `list` -> `get`) instead of carrying
everything in the prompt - Dynamiq's own cloud registry is not used.

This skeleton only proves the ABC contract compiles against the installed
`dynamiq==0.65.0` library; it returns empty/stub data. A real implementation
will read published `skill_versions` rows (+ MinIO object) scoped to the
company in context, with caching.
"""
from dynamiq.skills.registries.base import BaseSkillRegistry
from dynamiq.skills.types import SkillInstructions, SkillMetadata


class ConsoleSkillRegistry(BaseSkillRegistry):
    """Reads published skill versions from Postgres/MinIO (stubbed for now)."""

    def get_skills_metadata(self) -> list[SkillMetadata]:
        # Real implementation: SELECT name, description FROM published skills
        # for the company in the current request context.
        return []

    def get_skill_instructions(self, name: str) -> SkillInstructions:
        # Real implementation: fetch the published SKILL.md content (+ MinIO
        # object) for `name`, scoped to the company in context.
        raise NotImplementedError(f"ConsoleSkillRegistry.get_skill_instructions: skill {name!r} not found (stub)")
