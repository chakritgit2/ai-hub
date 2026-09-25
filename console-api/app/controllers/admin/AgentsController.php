<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Http\Response;

/**
 * /agents and /agents/{id}/versions* — agent specs and their published versions
 * (PRD §4.4-A, §8.1 `agents` + `agent_versions` tables).
 */
class AgentsController extends ControllerBase
{
    public function listAgents(): Response
    {
        return $this->notImplemented();
    }

    public function createAgent(): Response
    {
        return $this->notImplemented();
    }

    public function getAgent(string $id): Response
    {
        return $this->notImplemented();
    }

    public function updateAgent(string $id): Response
    {
        return $this->notImplemented();
    }

    public function cloneAgent(string $id): Response
    {
        return $this->notImplemented();
    }

    /** x-phase: 3 */
    public function exportAgent(string $id): Response
    {
        return $this->notImplemented();
    }

    /** x-phase: 3 */
    public function importAgent(string $id): Response
    {
        return $this->notImplemented();
    }

    public function listAgentVersions(string $id): Response
    {
        return $this->notImplemented();
    }

    public function createAgentVersion(string $id): Response
    {
        return $this->notImplemented();
    }

    public function getAgentVersion(string $id, string $vid): Response
    {
        return $this->notImplemented();
    }

    public function updateAgentVersion(string $id, string $vid): Response
    {
        return $this->notImplemented();
    }

    /**
     * Recompiles via ai-runtime (RuntimeClient::compileAgentSpec), sets is_published=true,
     * bumps dependent deployments' config_version (PRD §4.4-A).
     */
    public function publishAgentVersion(string $id, string $vid): Response
    {
        return $this->notImplemented();
    }
}
