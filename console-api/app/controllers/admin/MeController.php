<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Http\Response;

/**
 * GET /me — current user, their companies and roles (contracts/openapi/console-api.yaml,
 * PRD §9.1). AuthMiddleware verifies the SSO JWT and syncs console.users/company_members
 * before this action runs (PRD §7.1); see app/services/AuthService.php.
 */
class MeController extends ControllerBase
{
    public function getMe(): Response
    {
        $authUser = $this->getAuthUser();

        return $this->jsonResponse([
            'id' => $authUser['id'],
            'email' => $authUser['email'],
            'is_platform_admin' => $authUser['is_platform_admin'],
            'companies' => array_map(
                static fn (array $membership): array => [
                    'company_id' => $membership['company_id'],
                    'role' => $membership['role'],
                ],
                $authUser['companies']
            ),
        ]);
    }
}
