<?php

declare(strict_types=1);

namespace ConsoleApi\Middleware;

use Phalcon\Events\Event;
use Phalcon\Mvc\Dispatcher;

/**
 * Stub pass-through — writing to `audit_logs` (id, company_id, user_id, action, entity,
 * entity_id, diff, created_at — PRD §8.1) is not wired up yet.
 *
 * TODO: once AuthMiddleware attaches the authenticated user, record one row per
 * mutating request here (action derived from HTTP method + operationId, diff computed
 * from before/after state where applicable).
 */
class AuditMiddleware
{
    public function afterDispatch(Event $event, Dispatcher $dispatcher): void
    {
    }
}
