<?php

declare(strict_types=1);

namespace ConsoleApi\Services;

use RuntimeException;

/**
 * Verifies the SSO JWT issued by the existing Phalcon system (RS256 + JWKS, PRD §7.1/§7.6).
 *
 * Not implemented yet — AuthMiddleware currently passes every request through
 * unauthenticated (see app/middleware/AuthMiddleware.php). Wiring this up means:
 *   1. fetch + cache the JWKS from $jwksUrl (respecting `kid` and rotation, PRD §7.6),
 *   2. verify signature/exp/iss/aud with firebase/php-jwt,
 *   3. return the decoded claims (`sub`, `email`, `companies: [{ref, role}]`),
 *   4. console-api syncs `companies` and `company_members` on each login (PRD §7.1).
 */
class AuthService
{
    public function __construct(
        private readonly string $jwksUrl = '',
        private readonly string $issuer = '',
        private readonly string $audience = ''
    ) {
    }

    /**
     * @return array{sub: string, email: string, companies: array<int, array{ref: string, role: string}>}
     */
    public function verifySsoJwt(string $jwt): array
    {
        throw new RuntimeException('not_implemented: AuthService::verifySsoJwt (PRD §7.1/§7.6)');
    }
}
