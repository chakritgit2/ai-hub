<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Http\Response;

/**
 * /deployments — gateway-facing deployment configs (PRD §8.1 `deployments` table).
 */
class DeploymentsController extends ControllerBase
{
    public function listDeployments(): Response
    {
        return $this->notImplemented();
    }

    public function createDeployment(): Response
    {
        return $this->notImplemented();
    }

    public function getDeployment(string $id): Response
    {
        return $this->notImplemented();
    }

    public function updateDeployment(string $id): Response
    {
        return $this->notImplemented();
    }

    public function deleteDeployment(string $id): Response
    {
        return $this->notImplemented();
    }

    /** x-phase: 3 */
    public function promoteDeployment(string $id): Response
    {
        return $this->notImplemented();
    }
}
