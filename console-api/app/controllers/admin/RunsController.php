<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Http\Response;

/**
 * /runs — read-only run history (PRD §8.1 `runs`, `guardrail_events` tables, written by
 * ai-runtime/ai-gateway; console-api only reads them for the console UI).
 */
class RunsController extends ControllerBase
{
    public function listRuns(): Response
    {
        return $this->notImplemented();
    }

    public function getRun(string $id): Response
    {
        return $this->notImplemented();
    }

    public function getRunConversation(string $id): Response
    {
        return $this->notImplemented();
    }

    public function listRunGuardrailEvents(string $id): Response
    {
        return $this->notImplemented();
    }
}
