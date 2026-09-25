<?php

declare(strict_types=1);

namespace ConsoleApi\Tests\Unit;

use ConsoleApi\Controllers\Admin\HealthController;
use PHPUnit\Framework\TestCase;

/**
 * Sanity check for GET /healthz. HealthController never touches the DI container or
 * the database, so it can be exercised directly without booting the full application.
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
}
