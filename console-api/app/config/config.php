<?php

declare(strict_types=1);

use Dotenv\Dotenv;
use Phalcon\Config\Config;

$consoleApiRoot = dirname(__DIR__, 2);

if (file_exists($consoleApiRoot . '/.env')) {
    Dotenv::createImmutable($consoleApiRoot)->safeLoad();
}

/**
 * Small env() helper — reads $_ENV/$_SERVER/getenv() in that order, since phpdotenv
 * populates $_ENV by default but the runtime (php-fpm/CLI server) may already have
 * some of these set directly.
 */
$env = static function (string $key, $default = null) {
    $value = $_ENV[$key] ?? $_SERVER[$key] ?? getenv($key);

    return ($value === false || $value === null || $value === '') ? $default : $value;
};

return new Config([
    'app' => [
        'env' => $env('APP_ENV', 'local'),
        'debug' => filter_var($env('APP_DEBUG', 'true'), FILTER_VALIDATE_BOOLEAN),
    ],

    // console_app / console_platform roles per PRD §7.3, §8.1 and
    // db/migrations/pre/001_schemas_roles_extensions.sql (repo-level db/).
    'database' => [
        'host' => $env('DB_HOST', '127.0.0.1'),
        'port' => (int) $env('DB_PORT', 5432),
        'dbname' => $env('DB_NAME', 'ai_console'),
        'schema' => $env('DB_SCHEMA', 'console'),
        'username' => $env('DB_APP_USER', 'console_app'),
        'password' => $env('DB_APP_PASSWORD', ''),
        'platformUsername' => $env('DB_PLATFORM_USER', 'console_platform'),
        'platformPassword' => $env('DB_PLATFORM_PASSWORD', ''),
    ],

    // SSO JWT verification against the existing Phalcon system's JWKS (PRD §7.1/§7.6).
    'sso' => [
        'jwksUrl' => $env('SSO_JWKS_URL', ''),
        'issuer' => $env('SSO_ISSUER', ''),
        'audience' => $env('SSO_AUDIENCE', ''),
    ],

    // console-api's own signing key for runtime tokens + internal call tokens (PRD §7.6).
    'consoleJwt' => [
        'privateKeyPath' => $env('CONSOLE_JWT_PRIVATE_KEY_PATH', ''),
        'kid' => $env('CONSOLE_JWT_KID', ''),
        'jwksPath' => $env('CONSOLE_JWKS_PATH', '/admin/.well-known/jwks.json'),
    ],

    // ai-runtime internal base URL, reachable only from console-api pods (PRD §11 NetworkPolicy).
    'aiRuntime' => [
        'internalBaseUrl' => $env('AI_RUNTIME_INTERNAL_BASE_URL', 'http://ai-runtime:8081'),
        'publicBaseUrl' => $env('AI_RUNTIME_PUBLIC_BASE_URL', 'http://ai-runtime:8080'),
        'timeoutSeconds' => (int) $env('AI_RUNTIME_INTERNAL_TIMEOUT_SECONDS', 10),
    ],
]);
