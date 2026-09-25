<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Http\Response;

/**
 * /approvals — x-phase: 2 in the OpenAPI contract (PRD §8.1 `tool_approvals` table).
 */
class ApprovalsController extends ControllerBase
{
    public function listApprovals(): Response
    {
        return $this->notImplemented();
    }

    public function decideApproval(string $id): Response
    {
        return $this->notImplemented();
    }
}
