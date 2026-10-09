"""Console-owned skill registry (PRD §6.6a).

Skills are managed on the Skills page per company (Markdown `SKILL.md`, versioned/published)
and stored in `console.skills`/`console.skill_versions` — content lives directly in Postgres
for this slice (not on disk like Knowledge Bases' OKF files, `app.core.storage`: a
`SKILL.md` capped at 100 KB fits a `text` column fine — moving to file-backed storage is
the natural follow-up once `.zip`/scripts/attachments support is built, which is phase 3
per the PRD's own phasing).

`BaseSkillRegistry.get_skills_metadata`/`get_skill_instructions` are *synchronous* (dynamiq's
own ABC), but this app's DB layer is async-only — so this registry is never queried per call;
it's pre-loaded once via `get_console_skill_registry` (an async factory with an in-process TTL
cache, same dict+`time.monotonic()` pattern as `app.core.auth`'s `_jwks_cache`) and then only
serves synchronous in-memory lookups, exactly like dynamiq's own `FileSystem`/`Dynamiq`
reference registries populate their own `skills` list up front.

Wired into a live agent run by `app.services.runtime._execute_agent_run`, which scopes the
cached company-wide registry down to just the compiled agent's own declared skill names
before attaching it via `SkillsConfig` (see `app.services.compiler.build_agent`).
"""
import time

from dynamiq.skills.registries.base import BaseSkillRegistry
from dynamiq.skills.types import SkillInstructions, SkillMetadata, SkillRegistryError
from pydantic import Field

from app.services.skills import PublishedSkill, list_published_skills

_SKILL_REGISTRY_CACHE_TTL_SECONDS = 300
_skill_registry_cache: dict[str, tuple[float, "ConsoleSkillRegistry"]] = {}


class ConsoleSkillRegistry(BaseSkillRegistry):
    """Serves a pre-loaded list of a company's published skills (PRD §6.6a)."""

    skills: list[PublishedSkill] = Field(default_factory=list)

    def get_skills_metadata(self) -> list[SkillMetadata]:
        return [SkillMetadata(name=skill.name, description=skill.description) for skill in self.skills]

    def get_skill_instructions(self, name: str) -> SkillInstructions:
        skill = next((s for s in self.skills if s.name == name), None)
        if skill is None:
            raise SkillRegistryError(f"skill {name!r} not found")
        return SkillInstructions(name=skill.name, description=skill.description, instructions=skill.content)


async def get_console_skill_registry(company_id: str) -> ConsoleSkillRegistry:
    cached = _skill_registry_cache.get(company_id)
    if cached is not None and (time.monotonic() - cached[0]) < _SKILL_REGISTRY_CACHE_TTL_SECONDS:
        return cached[1]

    registry = ConsoleSkillRegistry(skills=await list_published_skills(company_id))
    _skill_registry_cache[company_id] = (time.monotonic(), registry)
    return registry
