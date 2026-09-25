<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Http\Response;

/**
 * /settings/* — per-company members list and egress allowlist (PRD §6.4, §8.1
 * `egress_allowlist` table).
 */
class SettingsController extends ControllerBase
{
    public function listCompanyUsers(): Response
    {
        return $this->notImplemented();
    }

    public function listEgressAllowlist(): Response
    {
        return $this->notImplemented();
    }

    public function createEgressAllowlistEntry(): Response
    {
        return $this->notImplemented();
    }

    public function deleteEgressAllowlistEntry(string $id): Response
    {
        return $this->notImplemented();
    }
}
