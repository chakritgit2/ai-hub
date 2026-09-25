<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Http\Response;

/**
 * /tools — x-phase: 2 in the OpenAPI contract (PRD §8.1 `tools` table). Registered now
 * so the route surface is stable; behavior lands in a later phase.
 */
class ToolsController extends ControllerBase
{
    public function listTools(): Response
    {
        return $this->notImplemented();
    }

    public function createTool(): Response
    {
        return $this->notImplemented();
    }

    public function updateTool(string $id): Response
    {
        return $this->notImplemented();
    }

    public function deleteTool(string $id): Response
    {
        return $this->notImplemented();
    }

    public function testTool(string $id): Response
    {
        return $this->notImplemented();
    }
}
