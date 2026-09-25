<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Http\Response;

/**
 * /evals — x-phase: 3 in the OpenAPI contract (PRD §8.1 `eval_runs`/`eval_results` tables).
 */
class EvalsController extends ControllerBase
{
    public function listEvalRuns(): Response
    {
        return $this->notImplemented();
    }

    public function createEvalRun(): Response
    {
        return $this->notImplemented();
    }

    public function getEvalRun(string $id): Response
    {
        return $this->notImplemented();
    }

    public function compareEvalRuns(): Response
    {
        return $this->notImplemented();
    }
}
