<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Http\Response;

/**
 * /api-keys — long-lived API keys used by external apps against the gateway
 * (PRD §8.1 `api_keys` table; resolved via `console.resolve_api_key()` at the gateway).
 */
class ApiKeysController extends ControllerBase
{
    public function listApiKeys(): Response
    {
        return $this->notImplemented();
    }

    public function createApiKey(): Response
    {
        return $this->notImplemented();
    }

    public function revokeApiKey(string $id): Response
    {
        return $this->notImplemented();
    }
}
