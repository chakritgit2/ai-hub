<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Http\Response;

/**
 * /skills — x-phase: 2 in the OpenAPI contract (PRD §8.1 `skills`/`skill_versions` tables).
 */
class SkillsController extends ControllerBase
{
    public function listSkills(): Response
    {
        return $this->notImplemented();
    }

    public function createSkill(): Response
    {
        return $this->notImplemented();
    }

    public function updateSkill(string $id): Response
    {
        return $this->notImplemented();
    }

    public function publishSkill(string $id): Response
    {
        return $this->notImplemented();
    }
}
