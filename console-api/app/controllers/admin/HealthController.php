<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Http\Response;
use Throwable;

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

    /**
     * GET /readyz — readiness probe, distinct from /healthz above on purpose: a pod can be
     * alive (PHP-FPM accepting connections) while still unable to serve a single real
     * request because Postgres is unreachable (e.g. mid-failover) - a liveness-only setup
     * would keep routing traffic to it instead of letting it fail out of the Service's
     * endpoint list. `SELECT 1` is the cheapest query that still proves a live connection
     * and a working query path, without touching any `console`/`runtime` table (and
     * therefore without needing CompanyContext/RLS at all - this never runs inside
     * runInCompanyTransaction()).
     */
    public function ready(): Response
    {
        try {
            $db = $this->getDI()->getShared('db');
            $db->fetchOne('SELECT 1');
        } catch (Throwable $exception) {
            return $this->jsonResponse(['status' => 'unavailable', 'error' => $exception->getMessage()], 503);
        }

        return $this->jsonResponse(['status' => 'ok']);
    }
}
