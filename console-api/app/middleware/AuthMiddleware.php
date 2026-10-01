<?php

declare(strict_types=1);

namespace ConsoleApi\Middleware;

use ConsoleApi\Services\AuthService;
use ConsoleApi\Services\InvalidSsoTokenException;
use Phalcon\Di\Di;
use Phalcon\Events\Event;
use Phalcon\Mvc\Dispatcher;

/**
 * Verifies the SSO JWT on every /admin/v1/* request and syncs the caller's user +
 * company memberships via AuthService (PRD §7.1). The decoded, synced record is attached
 * to the dispatcher as the `authUser` param for controllers (see ControllerBase::getAuthUser()).
 *
 * GET /healthz and GET /admin/.well-known/jwks.json are the only exempt routes —
 * k8s liveness/readiness probes send no Authorization header (PRD §11), and the JWKS
 * endpoint must be reachable by anything verifying console-api-issued tokens, which
 * obviously can't itself present one.
 */
class AuthMiddleware
{
    private const EXEMPT_CONTROLLERS = ['Health', 'Jwks'];

    public function beforeDispatch(Event $event, Dispatcher $dispatcher): bool
    {
        if (in_array($dispatcher->getControllerName(), self::EXEMPT_CONTROLLERS, true)) {
            return true;
        }

        $header = $_SERVER['HTTP_AUTHORIZATION'] ?? '';
        if (!preg_match('/^Bearer\s+(\S+)$/i', $header, $matches)) {
            $this->unauthorized('missing_authorization_header');

            return false;
        }

        $di = Di::getDefault();
        /** @var AuthService $authService */
        $authService = $di->getShared('authService');

        try {
            $claims = $authService->verifySsoJwt($matches[1]);
            $authUser = $authService->syncUser($claims);
        } catch (InvalidSsoTokenException $exception) {
            $this->unauthorized($exception->getMessage());

            return false;
        }

        $dispatcher->setParam('authUser', $authUser);

        return true;
    }

    /**
     * Mutates the shared `response` service rather than returning a new Response — a
     * middleware's `beforeDispatch` return value isn't captured as the dispatcher's
     * returned value, so Application::handle() falls back to this shared instance once
     * dispatching stops (see TraceparentMiddleware/CompanyContextMiddleware for the same
     * shared-service convention).
     */
    private function unauthorized(string $message): void
    {
        $response = Di::getDefault()->getShared('response');
        $response->setStatusCode(401, 'Unauthorized');
        $response->setContentType('application/json');
        $response->setJsonContent(['error' => 'unauthorized', 'message' => $message]);
    }
}
