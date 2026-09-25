<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Http\Response;

/**
 * /dashboard — aggregate usage/cost summary for the current company (PRD §9.1
 * `DashboardSummary`), computed from `runs`/`guardrail_events` on the read replica.
 */
class DashboardController extends ControllerBase
{
    public function getDashboardSummary(): Response
    {
        return $this->notImplemented();
    }
}
