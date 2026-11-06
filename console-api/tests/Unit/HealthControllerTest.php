<?php

declare(strict_types=1);

namespace ConsoleApi\Tests\Unit;

use ConsoleApi\Controllers\Admin\HealthController;
use Phalcon\Di\Di;
use PHPUnit\Framework\TestCase;
use RuntimeException;

/**
 * Sanity check for GET /healthz and GET /readyz. index() never touches the DI container
 * or the database, so it can be exercised directly without booting the full application;
 * ready() does query the database, so its tests inject a fake 'db' service into a bare DI
 * container instead of needing a real Postgres connection.
 */
final class HealthControllerTest extends TestCase
{
    public function testIndexReturnsHttp200WithOkStatus(): void
    {
        $controller = new HealthController();

        $response = $controller->index();

        self::assertSame(200, $response->getStatusCode());
        self::assertSame(
            ['status' => 'ok'],
            json_decode((string) $response->getContent(), true)
        );
    }

    public function testReadyReturnsHttp200WhenTheDatabaseResponds(): void
    {
        $di = new Di();
        $di->setShared('db', fn () => new class {
            public function fetchOne(string $sql): array
            {
                return ['?column?' => 1];
            }
        });
        $controller = new HealthController();
        $controller->setDI($di);

        $response = $controller->ready();

        self::assertSame(200, $response->getStatusCode());
        self::assertSame(
            ['status' => 'ok'],
            json_decode((string) $response->getContent(), true)
        );
    }

    public function testReadyReturnsHttp503WhenTheDatabaseIsUnreachable(): void
    {
        $di = new Di();
        $di->setShared('db', fn () => new class {
            public function fetchOne(string $sql): array
            {
                throw new RuntimeException('connection refused');
            }
        });
        $controller = new HealthController();
        $controller->setDI($di);

        $response = $controller->ready();

        self::assertSame(503, $response->getStatusCode());
        $body = json_decode((string) $response->getContent(), true);
        self::assertSame('unavailable', $body['status']);
        self::assertSame('connection refused', $body['error']);
    }
}
