<?php

declare(strict_types=1);

namespace ConsoleApi\Services;

use RuntimeException;

/**
 * Derives the public JWKS from console-api's own signing key (PRD §7.6) — served by
 * JwksController at GET /admin/.well-known/jwks.json (public, no auth: ai-runtime and
 * the existing SSO system's verifiers all need to reach it unauthenticated).
 */
class ConsoleJwks
{
    public function __construct(
        private readonly string $privateKeyPath,
        private readonly string $kid
    ) {
    }

    /**
     * @return array{keys: array<int, array<string, string>>}
     */
    public function toArray(): array
    {
        if ($this->privateKeyPath === '' || !is_readable($this->privateKeyPath)) {
            throw new RuntimeException(
                "ConsoleJwks: private key not readable at '{$this->privateKeyPath}' "
                . '— run `php bin/generate-jwt-key.php`.'
            );
        }

        $privateKey = openssl_pkey_get_private(file_get_contents($this->privateKeyPath));
        $details = openssl_pkey_get_details($privateKey);

        return ['keys' => [[
            'kty' => 'RSA',
            'kid' => $this->kid,
            'alg' => 'RS256',
            'use' => 'sig',
            'n' => $this->base64UrlEncode($details['rsa']['n']),
            'e' => $this->base64UrlEncode($details['rsa']['e']),
        ]]];
    }

    private function base64UrlEncode(string $binary): string
    {
        return rtrim(strtr(base64_encode($binary), '+/', '-_'), '=');
    }
}
