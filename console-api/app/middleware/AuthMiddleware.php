<?php

declare(strict_types=1);

namespace ConsoleApi\Middleware;

use Phalcon\Events\Event;
use Phalcon\Mvc\Dispatcher;

/**
 * Stub pass-through — SSO JWT verification (PRD §7.1/§7.6) is not wired up yet.
 *
 * TODO: read the `Authorization: Bearer <jwt>` header, verify it via
 * AuthService::verifySsoJwt(), reject with 401 on failure, and attach the decoded
 * user/company claims to the request context for controllers + AuditMiddleware.
 */
class AuthMiddleware
{
    public function beforeDispatch(Event $event, Dispatcher $dispatcher): bool
    {
        return true;
    }
}
