<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Http\Response;

/**
 * GET /me — current user, their companies and roles (contracts/openapi/console-api.yaml,
 * PRD §9.1). AuthService::verifySsoJwt() is not implemented yet (see app/services/AuthService.php),
 * so this returns a hardcoded shape matching the OpenAPI `Me` schema for skeleton/local dev.
 */
class MeController extends ControllerBase
{
    public function getMe(): Response
    {
        return $this->jsonResponse([
            'id' => '00000000-0000-0000-0000-000000000000',
            'email' => 'dev@example.com',
            'is_platform_admin' => false,
            'companies' => [
                [
                    'company_id' => '00000000-0000-0000-0000-000000000001',
                    'role' => 'admin',
                ],
            ],
        ]);
    }
}
