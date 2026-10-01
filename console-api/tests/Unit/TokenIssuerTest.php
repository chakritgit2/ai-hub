<?php

declare(strict_types=1);

namespace ConsoleApi\Tests\Unit;

use ConsoleApi\Services\ConsoleJwks;
use ConsoleApi\Services\TokenIssuer;
use Firebase\JWT\JWK;
use Firebase\JWT\JWT;
use PHPUnit\Framework\TestCase;

/**
 * Exercises TokenIssuer + ConsoleJwks together: signs with TokenIssuer, verifies with
 * the JWKS ConsoleJwks derives from the same key — the same round trip ai-runtime does
 * for real (PRD §7.6), just without a network hop to fetch the JWKS.
 */
final class TokenIssuerTest extends TestCase
{
    private const ISSUER = 'console-api';
    private const RUNTIME_AUD = 'ai-runtime';
    private const INTERNAL_AUD = 'ai-internal';
    private const KID = 'test-console-key';

    private string $keyPath;

    protected function setUp(): void
    {
        $this->keyPath = tempnam(sys_get_temp_dir(), 'console-jwt-test-') . '.pem';

        $key = openssl_pkey_new(['private_key_bits' => 2048, 'private_key_type' => OPENSSL_KEYTYPE_RSA]);
        self::assertNotFalse($key, 'openssl_pkey_new() failed — is the openssl extension enabled?');
        openssl_pkey_export($key, $pem);
        file_put_contents($this->keyPath, $pem);
    }

    protected function tearDown(): void
    {
        @unlink($this->keyPath);
    }

    public function testRuntimeTokenRoundTripsThroughTheDerivedJwks(): void
    {
        $issuer = $this->makeIssuer();
        $jwks = new ConsoleJwks($this->keyPath, self::KID);

        $result = $issuer->issueRuntimeToken('company-1', 'user-1', 'developer', 'agent-version-1');
        self::assertSame(300, $result['expires_in']);

        $payload = $this->decode($result['token'], $jwks, self::RUNTIME_AUD);

        self::assertSame(self::ISSUER, $payload->iss);
        self::assertSame(self::RUNTIME_AUD, $payload->aud);
        self::assertSame('company-1', $payload->company_id);
        self::assertSame('user-1', $payload->user_id);
        self::assertSame('developer', $payload->role);
        self::assertSame('agent-version-1', $payload->agent_version_id);
        self::assertEqualsWithDelta(time() + 300, $payload->exp, 5);
    }

    public function testInternalTokenHasA60SecondTtlAndNoUserClaims(): void
    {
        $issuer = $this->makeIssuer();
        $jwks = new ConsoleJwks($this->keyPath, self::KID);

        $token = $issuer->issueInternalToken();
        $payload = $this->decode($token, $jwks, self::INTERNAL_AUD);

        self::assertSame(self::ISSUER, $payload->iss);
        self::assertSame(self::INTERNAL_AUD, $payload->aud);
        self::assertFalse(property_exists($payload, 'company_id'));
        self::assertEqualsWithDelta(time() + 60, $payload->exp, 5);
    }

    public function testRuntimeTokenIsRejectedUnderTheInternalAudience(): void
    {
        $issuer = $this->makeIssuer();
        $jwks = new ConsoleJwks($this->keyPath, self::KID);

        $result = $issuer->issueRuntimeToken('company-1', 'user-1', 'developer', 'agent-version-1');

        $this->expectException(\UnexpectedValueException::class);
        $this->decode($result['token'], $jwks, self::INTERNAL_AUD);
    }

    private function makeIssuer(): TokenIssuer
    {
        return new TokenIssuer($this->keyPath, self::KID, self::ISSUER, self::RUNTIME_AUD, self::INTERNAL_AUD);
    }

    private function decode(string $jwt, ConsoleJwks $jwks, string $expectedAudience): \stdClass
    {
        $keys = JWK::parseKeySet($jwks->toArray());
        JWT::$leeway = 0;
        $payload = JWT::decode($jwt, $keys);

        if ($payload->aud !== $expectedAudience) {
            throw new \UnexpectedValueException("aud mismatch: expected {$expectedAudience}, got {$payload->aud}");
        }

        return $payload;
    }
}
