<?php

declare(strict_types=1);

namespace ConsoleApi\Services;

use Firebase\JWT\JWT;
use RuntimeException;

/**
 * Issues console-api's two signed JWT types (PRD §7.6) — both verified by ai-runtime
 * against the same JWKS (JwksController) and distinguished only by `aud`:
 *
 *   - Runtime token (PRD §4.4-B): 5 minutes, carries company_id/user_id/role/
 *     agent_version_id, exchanged by the web app for Playground access.
 *   - Internal call token (PRD §9.2): 60 seconds, aud=ai-internal, no claims beyond
 *     standard ones — authorizes console-api -> ai-runtime /internal/v1/* calls,
 *     which also carry X-Company-Id/traceparent headers (RuntimeClient's job, not
 *     implemented yet — this method exists so that work doesn't need to touch signing).
 */
class TokenIssuer
{
    private const RUNTIME_TOKEN_TTL_SECONDS = 300;
    private const INTERNAL_TOKEN_TTL_SECONDS = 60;

    public function __construct(
        private readonly string $privateKeyPath,
        private readonly string $kid,
        private readonly string $issuer,
        private readonly string $runtimeTokenAudience,
        private readonly string $internalJwtAudience
    ) {
    }

    /**
     * @return array{token: string, expires_in: int}
     */
    public function issueRuntimeToken(string $companyId, string $userId, string $role, string $agentVersionId): array
    {
        $now = time();
        $token = JWT::encode([
            'iss' => $this->issuer,
            'aud' => $this->runtimeTokenAudience,
            'company_id' => $companyId,
            'user_id' => $userId,
            'role' => $role,
            'agent_version_id' => $agentVersionId,
            'iat' => $now,
            'exp' => $now + self::RUNTIME_TOKEN_TTL_SECONDS,
        ], $this->privateKey(), 'RS256', $this->kid);

        return ['token' => $token, 'expires_in' => self::RUNTIME_TOKEN_TTL_SECONDS];
    }

    public function issueInternalToken(): string
    {
        $now = time();

        return JWT::encode([
            'iss' => $this->issuer,
            'aud' => $this->internalJwtAudience,
            'iat' => $now,
            'exp' => $now + self::INTERNAL_TOKEN_TTL_SECONDS,
        ], $this->privateKey(), 'RS256', $this->kid);
    }

    private function privateKey(): string
    {
        if ($this->privateKeyPath === '' || !is_readable($this->privateKeyPath)) {
            throw new RuntimeException(
                "TokenIssuer: private key not readable at '{$this->privateKeyPath}' "
                . '— run `php bin/generate-jwt-key.php`.'
            );
        }

        return file_get_contents($this->privateKeyPath);
    }
}
