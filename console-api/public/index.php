<?php

declare(strict_types=1);

use ConsoleApi\Middleware\AuditMiddleware;
use ConsoleApi\Middleware\AuthMiddleware;
use ConsoleApi\Middleware\CompanyContextMiddleware;
use ConsoleApi\Middleware\TraceparentMiddleware;
use Phalcon\Events\Manager as EventsManager;
use Phalcon\Http\Response;
use Phalcon\Mvc\Application;
use Phalcon\Mvc\Dispatcher\Exception as DispatcherException;

error_reporting(E_ALL);
ini_set('display_errors', '0');

define('CONSOLE_API_ROOT', dirname(__DIR__));

require CONSOLE_API_ROOT . '/vendor/autoload.php';

/**
 * JSON error handler — anything that escapes the try/catch below (fatal errors during
 * bootstrap, before a Response object exists) still comes back as JSON, not an HTML trace.
 */
set_exception_handler(static function (\Throwable $exception): void {
    http_response_code(500);
    header('Content-Type: application/json');
    echo json_encode([
        'error' => 'internal_error',
        'message' => $exception->getMessage(),
    ]);
});

$di = require CONSOLE_API_ROOT . '/app/config/services.php';

// Middleware chain (PRD §4, §7): Traceparent -> Auth (stub) -> CompanyContext -> Audit (stub).
// NB: these must be non-static closures — Phalcon\Di rebinds `$this` (the container) into
// every service definition closure, which silently fails (and returns null) for `static fn`.
$di->setShared('traceparentMiddleware', fn () => new TraceparentMiddleware());
$di->setShared('authMiddleware', fn () => new AuthMiddleware());
$di->setShared('companyContextMiddleware', fn () => new CompanyContextMiddleware());
$di->setShared('auditMiddleware', fn () => new AuditMiddleware());

$eventsManager = new EventsManager();
$eventsManager->attach('dispatch:beforeDispatch', $di->getShared('traceparentMiddleware'));
$eventsManager->attach('dispatch:beforeDispatch', $di->getShared('authMiddleware'));
$eventsManager->attach('dispatch:beforeDispatch', $di->getShared('companyContextMiddleware'));
$eventsManager->attach('dispatch:afterDispatch', $di->getShared('auditMiddleware'));

/** @var \Phalcon\Mvc\Dispatcher $dispatcher */
$dispatcher = $di->getShared('dispatcher');
$dispatcher->setEventsManager($eventsManager);

$application = new Application($di);
// Pure JSON API: every action returns a Phalcon\Http\Response directly, so the
// implicit Volt/PHP view rendering step is switched off entirely.
$application->useImplicitView(false);

try {
    $response = $application->handle($_SERVER['REQUEST_URI'] ?? '/');
    $response->send();
} catch (DispatcherException $exception) {
    // Router had no matching route, or the resolved controller/action doesn't exist.
    $response = new Response();
    $response->setStatusCode(404, 'Not Found');
    $response->setContentType('application/json');
    $response->setJsonContent(['error' => 'not_found']);
    $response->send();
} catch (\Throwable $exception) {
    $response = new Response();
    $response->setStatusCode(500, 'Internal Server Error');
    $response->setContentType('application/json');
    $response->setJsonContent([
        'error' => 'internal_error',
        'message' => $exception->getMessage(),
    ]);
    $response->send();
}
