-- Gives ai-runtime a read path to each company's published skills (PRD §6.6a), same
-- SECURITY DEFINER pattern as console.resolve_tool (012). Returns one row per skill — its
-- latest *published* version only (a draft-only skill, or a skill whose latest version was
-- never published, is excluded entirely), via a lateral join picking the highest version_no
-- among is_published = true rows.

CREATE OR REPLACE FUNCTION console.resolve_published_skills(p_company_id uuid)
RETURNS TABLE(name varchar, description varchar, content text)
LANGUAGE sql
SECURITY DEFINER
SET search_path = console, pg_temp
AS $$
  SELECT s.name, s.description, sv.content
  FROM console.skills s
  JOIN LATERAL (
    SELECT content FROM console.skill_versions
    WHERE skill_id = s.id AND is_published = true
    ORDER BY version_no DESC LIMIT 1
  ) sv ON true
  WHERE s.company_id = p_company_id;
$$;

REVOKE ALL ON FUNCTION console.resolve_published_skills(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION console.resolve_published_skills(uuid) TO ai_app;
