<?php

declare(strict_types=1);

namespace ConsoleApi\Services;

use RuntimeException;

/**
 * Thrown by AuthService::verifySsoJwt() when the presented SSO JWT is missing, malformed,
 * expired, or fails signature/issuer/audience checks. AuthMiddleware maps this to HTTP 401
 * (PRD §7.1/§7.6) — as opposed to a plain RuntimeException, which signals SSO misconfiguration
 * or a JWKS fetch failure and is left to bubble up as a 500.
 */
class InvalidSsoTokenException extends RuntimeException
{
}
