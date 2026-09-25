<?php

declare(strict_types=1);

// console-api/db/migrations -> ../.. -> console-api root.
$consoleApiRoot = __DIR__ . '/../..';

require_once $consoleApiRoot . '/vendor/autoload.php';

if (file_exists($consoleApiRoot . '/.env')) {
    \Dotenv\Dotenv::createImmutable($consoleApiRoot)->safeLoad();
}

$env = static function (string $key, $default = null) {
    $value = $_ENV[$key] ?? $_SERVER[$key] ?? getenv($key);

    return ($value === false || $value === null || $value === '') ? $default : $value;
};

return [
    'paths' => [
        'migrations' => __DIR__,
        'seeds' => __DIR__ . '/seeds',
    ],
    'migration_base_class' => \Phinx\Migration\AbstractMigration::class,
    'environments' => [
        'default_migration_table' => 'phinxlog',
        'default_environment' => 'local',
        'local' => [
            'adapter' => 'pgsql',
            'host' => $env('DB_HOST', '127.0.0.1'),
            'name' => $env('DB_NAME', 'ai_console'),
            'port' => (int) $env('DB_PORT', 5432),
            // Migrations connect as db_owner: it owns every table so app roles never do
            // (PRD §7.3). console_app/console_platform only get GRANTs, applied in
            // db/migrations/post/ once these tables exist.
            'user' => $env('DB_OWNER_USER', 'db_owner'),
            'pass' => $env('DB_OWNER_PASSWORD', 'changeme_local_dev_only'),
            'charset' => 'utf8',
            // Tables live in the `console` schema (PRD §8.1), created by
            // db/migrations/pre/001_schemas_roles_extensions.sql before this runs.
            'schema' => $env('DB_SCHEMA', 'console'),
        ],
    ],
    'version_order' => 'creation',
];
