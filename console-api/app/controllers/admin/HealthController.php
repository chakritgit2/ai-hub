<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Http\Response;

/**
 * GET /healthz — liveness probe. Not part of contracts/openapi/console-api.yaml; ops-only
 * and never exposed via ingress (PRD §9). Deliberately does not touch the database so it
 * stays meaningful even when Postgres is unreachable.
 */
class HealthController extends ControllerBase
{
    public function index(): Response
    {
        return $this->jsonResponse(['status' => 'ok']);
    }
}
