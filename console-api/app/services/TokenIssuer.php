<?php

declare(strict_types=1);

namespace ConsoleApi\Services;

use RuntimeException;

/**
 * Issues the 5-minute Playground runtime JWT that the web app exchanges for streaming
 * access to ai-runtime (PRD §4.4-B, §7.6): `POST /admin/v1/runtime-token` ->
 * RuntimeTokenController::issueRuntimeToken() -> this class.
 *
 * Not implemented yet — real signing needs firebase/php-jwt + the RS256 private key at
 * $privateKeyPath, with `kid` set from console-api's own JWKS (`/admin/.well-known/jwks.json`).
 */
class TokenIssuer
{
    public function __construct(
        private readonly string $privateKeyPath = '',
        private readonly string $kid = ''
    ) {
    }

    public function issueRuntimeToken(string $agentVersionId): string
    {
        throw new RuntimeException('not_implemented: TokenIssuer::issueRuntimeToken (PRD §4.4-B/§7.6)');
    }
}
