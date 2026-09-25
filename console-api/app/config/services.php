<?php

declare(strict_types=1);

use ConsoleApi\Services\AuthService;
use ConsoleApi\Services\CompanyContext;
use ConsoleApi\Services\RuntimeClient;
use ConsoleApi\Services\TokenIssuer;
use Phalcon\Db\Adapter\Pdo\Postgresql as PdoPostgresql;
use Phalcon\Di\FactoryDefault;
use Phalcon\Mvc\Dispatcher;
use Phalcon\Mvc\Router;
use Phalcon\Mvc\Url as UrlResolver;

$di = new FactoryDefault();

$di->setShared('config', function () {
    return require __DIR__ . '/config.php';
});

// PDO pgsql connection using the `console_app` role (PRD §7.3/§8.1). Lazily instantiated —
// never touched by routes that don't need it (e.g. GET /healthz).
$di->setShared('db', function () {
    /** @var \Phalcon\Config\Config $config */
    $config = $this->getShared('config')->database;

    return new PdoPostgresql([
        'host' => $config->host,
        'port' => (string) $config->port,
        'dbname' => $config->dbname,
        'username' => $config->username,
        'password' => $config->password,
        'options' => [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        ],
    ]);
});

$di->setShared('router', function () {
    $router = new Router(false);
    $register = require __DIR__ . '/routes.php';
    $register($router);

    return $router;
});

$di->setShared('dispatcher', function () {
    $dispatcher = new Dispatcher();
    $dispatcher->setDefaultNamespace('ConsoleApi\\Controllers\\Admin');
    // Controller method names match OpenAPI operationIds exactly (getMe, testConnection, ...),
    // with no implicit "Action" suffix.
    $dispatcher->setActionSuffix('');

    return $dispatcher;
});

$di->setShared('url', function () {
    $url = new UrlResolver();
    $url->setBaseUri('/');

    return $url;
});

// Holds the X-Company-Id header for the current request (PRD §7.2/§7.3).
$di->setShared('companyContext', function () {
    return new CompanyContext();
});

// Stub — SSO JWT verification (PRD §7.1/§7.6).
$di->setShared('authService', function () {
    /** @var \Phalcon\Config\Config $config */
    $config = $this->getShared('config')->sso;

    return new AuthService((string) $config->jwksUrl, (string) $config->issuer, (string) $config->audience);
});

// Stub — console-api -> ai-runtime /internal/v1/* Guzzle client (PRD §7.6).
$di->setShared('runtimeClient', function () {
    /** @var \Phalcon\Config\Config $config */
    $config = $this->getShared('config')->aiRuntime;

    return new RuntimeClient((string) $config->internalBaseUrl, (int) $config->timeoutSeconds);
});

// Stub — issues the 5-minute Playground runtime JWT (PRD §4.4-B/§7.6).
$di->setShared('tokenIssuer', function () {
    /** @var \Phalcon\Config\Config $config */
    $config = $this->getShared('config')->consoleJwt;

    return new TokenIssuer((string) $config->privateKeyPath, (string) $config->kid);
});

return $di;
