<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Http\Response;

/**
 * /connections — company-scoped LLM/tool connections (PRD §7.4, §8.1 `connections` table).
 * Secrets are never persisted by console-api; putConnectionSecret forwards internally to
 * ai-runtime's `PUT /internal/v1/connections/{id}/secret`.
 */
class ConnectionsController extends ControllerBase
{
    public function listConnections(): Response
    {
        return $this->notImplemented();
    }

    public function createConnection(): Response
    {
        return $this->notImplemented();
    }

    public function getConnection(string $id): Response
    {
        return $this->notImplemented();
    }

    public function updateConnection(string $id): Response
    {
        return $this->notImplemented();
    }

    public function deleteConnection(string $id): Response
    {
        return $this->notImplemented();
    }

    public function putConnectionSecret(string $id): Response
    {
        return $this->notImplemented();
    }

    public function testConnection(string $id): Response
    {
        return $this->notImplemented();
    }
}
