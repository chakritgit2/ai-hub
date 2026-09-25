<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Http\Response;

/**
 * POST /runtime-token — issues a 5-minute Playground runtime JWT (PRD §4.4-B, §7.6).
 * Backed by ConsoleApi\Services\TokenIssuer, not implemented yet.
 */
class RuntimeTokenController extends ControllerBase
{
    public function issueRuntimeToken(): Response
    {
        return $this->notImplemented();
    }
}
