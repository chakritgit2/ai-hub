<?php

declare(strict_types=1);

namespace ConsoleApi\Middleware;

use ConsoleApi\Services\AuditLogger;
use ConsoleApi\Services\CompanyContext;
use Phalcon\Di\Di;
use Phalcon\Events\Event;
use Phalcon\Http\ResponseInterface;
use Phalcon\Mvc\Dispatcher;

/**
 * Writes one `console.audit_logs` row per successful mutating request (PRD §8.1/§8.2):
 * action = the dispatched method (== the OpenAPI operationId, see routes.php), entity =
 * controller name, entity_id = the route's `id`/`vid` param when present.
 *
 * Only 2xx responses are audited — a failed/stub (501, 4xx) request didn't actually
 * change anything, so there's nothing to log yet. `diff` is left null for now: computing
 * a real before/after diff needs each mutating controller to capture prior state itself,
 * which none of them do yet (every mutating controller is still a 501 stub — see
 * console-api/README.md).
 */
class AuditMiddleware
{
    private const MUTATING_METHODS = ['POST', 'PUT', 'PATCH', 'DELETE'];

    /** No user/company context to attach anything to, and nothing mutates here anyway. */
    private const EXEMPT_CONTROLLERS = ['Health', 'Jwks'];

    public function afterDispatch(Event $event, Dispatcher $dispatcher): void
    {
        $method = $_SERVER['REQUEST_METHOD'] ?? '';
        if (!in_array($method, self::MUTATING_METHODS, true)) {
            return;
        }

        $controllerName = (string) $dispatcher->getControllerName();
        if (in_array($controllerName, self::EXEMPT_CONTROLLERS, true)) {
            return;
        }

        if (!$this->wasSuccessful($dispatcher)) {
            return;
        }

        $di = Di::getDefault();
        /** @var CompanyContext $companyContext */
        $companyContext = $di->getShared('companyContext');
        /** @var array{id: string, ...}|null $authUser */
        $authUser = $dispatcher->getParam('authUser');

        $entityId = $dispatcher->getParam('id') ?? $dispatcher->getParam('vid');

        /** @var AuditLogger $auditLogger */
        $auditLogger = $di->getShared('auditLogger');
        $auditLogger->log(
            $companyContext->hasCompanyId() ? $companyContext->getCompanyId() : null,
            $authUser['id'] ?? null,
            (string) $dispatcher->getActionName(),
            $controllerName,
            $entityId !== null ? (string) $entityId : null
        );
    }

    /**
     * A controller action's returned Response is what Application::handle() actually
     * sends (see AuthMiddleware's note on the same mechanism) — the shared `response`
     * service is only the fallback for a return value that isn't one.
     */
    private function wasSuccessful(Dispatcher $dispatcher): bool
    {
        $returned = $dispatcher->getReturnedValue();
        $response = $returned instanceof ResponseInterface
            ? $returned
            : Di::getDefault()->getShared('response');

        $statusCode = (int) ($response->getStatusCode() ?? 200);

        return $statusCode >= 200 && $statusCode < 300;
    }
}
