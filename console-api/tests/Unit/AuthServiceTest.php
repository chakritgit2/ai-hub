<?php

declare(strict_types=1);

namespace ConsoleApi\Tests\Unit;

use ConsoleApi\Services\AuditLogger;
use ConsoleApi\Services\AuthService;
use ConsoleApi\Services\InvalidSsoTokenException;
use Firebase\JWT\JWT;
use GuzzleHttp\Client;
use GuzzleHttp\Handler\MockHandler;
use GuzzleHttp\HandlerStack;
use GuzzleHttp\Psr7\Response;
use OpenSSLAsymmetricKey;
use Phalcon\Db\Adapter\Pdo\AbstractPdo;
use PHPUnit\Framework\TestCase;

/**
 * Exercises AuthService::verifySsoJwt() against a self-signed RSA keypair standing in for
 * the existing Phalcon system's SSO key, with a mocked JWKS HTTP response — no live SSO
 * endpoint or database needed (PRD §7.1/§7.6). syncUser() needs a real Postgres connection
 * and is left to manual/integration testing (see db/contract_tests/README.md).
 *
 * Each test uses its own jwksUrl so AuthService's static per-process JWKS cache can't leak
 * a keypair from one test into another.
 */
final class AuthServiceTest extends TestCase
{
    private const ISSUER = 'https://sso.example.internal';
    private const AUDIENCE = 'dynamiq-console';

    public function testValidTokenReturnsDecodedClaims(): void
    {
        $service = $this->makeService($privateKey, $jwksUrl);

        $jwt = $this->issueToken($privateKey, [
            'iss' => self::ISSUER,
            'aud' => self::AUDIENCE,
            'sub' => 'sso-user-123',
            'email' => 'alice@example.com',
            'companies' => [['ref' => 'acme', 'role' => 'admin']],
            'exp' => time() + 300,
        ]);

        $claims = $service->verifySsoJwt($jwt);

        self::assertSame('sso-user-123', $claims['sub']);
        self::assertSame('alice@example.com', $claims['email']);
        self::assertSame([['ref' => 'acme', 'role' => 'admin']], $claims['companies']);
    }

    public function testExpiredTokenIsRejected(): void
    {
        $service = $this->makeService($privateKey, $jwksUrl);

        $jwt = $this->issueToken($privateKey, [
            'iss' => self::ISSUER,
            'aud' => self::AUDIENCE,
            'sub' => 'sso-user-123',
            'email' => 'alice@example.com',
            'companies' => [],
            'exp' => time() - 60,
        ]);

        $this->expectException(InvalidSsoTokenException::class);
        $service->verifySsoJwt($jwt);
    }

    public function testUnexpectedIssuerIsRejected(): void
    {
        $service = $this->makeService($privateKey, $jwksUrl);

        $jwt = $this->issueToken($privateKey, [
            'iss' => 'https://not-the-configured-issuer.example',
            'aud' => self::AUDIENCE,
            'sub' => 'sso-user-123',
            'email' => 'alice@example.com',
            'companies' => [],
            'exp' => time() + 300,
        ]);

        $this->expectException(InvalidSsoTokenException::class);
        $service->verifySsoJwt($jwt);
    }

    public function testMalformedCompaniesClaimIsRejected(): void
    {
        $service = $this->makeService($privateKey, $jwksUrl);

        $jwt = $this->issueToken($privateKey, [
            'iss' => self::ISSUER,
            'aud' => self::AUDIENCE,
            'sub' => 'sso-user-123',
            'email' => 'alice@example.com',
            'companies' => [['ref' => 'acme']], // missing "role"
            'exp' => time() + 300,
        ]);

        $this->expectException(InvalidSsoTokenException::class);
        $service->verifySsoJwt($jwt);
    }

    public function testSyncUserAuditsEachSkippedCompanyRef(): void
    {
        /** @var AbstractPdo&\PHPUnit\Framework\MockObject\MockObject $db */
        $db = $this->createMock(AbstractPdo::class);
        // 1st fetchOne: existing user lookup (found) — 2nd: company lookup (not found,
        // ref has no matching console.companies.external_ref). fetchOne()'s declared
        // return type is `array`, so "not found" is an empty array here, never false —
        // see the note in AuthService::syncUser().
        $db->method('fetchOne')->willReturnOnConsecutiveCalls(
            ['id' => 'user-1', 'is_platform_admin' => false],
            []
        );
        $db->expects(self::never())->method('begin');

        /** @var AuditLogger&\PHPUnit\Framework\MockObject\MockObject $auditLogger */
        $auditLogger = $this->createMock(AuditLogger::class);
        $auditLogger->expects(self::once())->method('log')->with(
            null,
            'user-1',
            'sso_login_company_skipped',
            'companies',
            'unregistered-co',
            ['role' => 'admin']
        );

        $service = new AuthService('https://sso.example.internal/jwks.json', '', '', $db, $auditLogger);

        $result = $service->syncUser([
            'sub' => 'sso-user-1',
            'email' => 'alice@example.com',
            'companies' => [['ref' => 'unregistered-co', 'role' => 'admin']],
        ]);

        self::assertSame(['unregistered-co'], $result['skipped_company_refs']);
        self::assertSame([], $result['companies']);
    }

    /**
     * Generates a fresh RSA keypair, wires a Guzzle MockHandler that serves its JWKS for
     * $jwksUrl (out-param), and returns an AuthService pointed at it. $privateKey is also
     * an out-param so callers can sign tokens against the same key.
     */
    private function makeService(?OpenSSLAsymmetricKey &$privateKey, ?string &$jwksUrl): AuthService
    {
        static $counter = 0;
        $counter++;
        $jwksUrl = 'https://sso.example.internal/.well-known/jwks.json?instance=' . $counter;

        $privateKey = openssl_pkey_new([
            'private_key_bits' => 2048,
            'private_key_type' => OPENSSL_KEYTYPE_RSA,
        ]);
        self::assertInstanceOf(OpenSSLAsymmetricKey::class, $privateKey, 'openssl RSA keygen failed');

        $details = openssl_pkey_get_details($privateKey);
        self::assertIsArray($details);

        $jwks = [
            'keys' => [[
                'kty' => 'RSA',
                'kid' => 'test-key-1',
                'alg' => 'RS256',
                'use' => 'sig',
                'n' => $this->base64UrlEncode($details['rsa']['n']),
                'e' => $this->base64UrlEncode($details['rsa']['e']),
            ]],
        ];

        $mock = new MockHandler([new Response(200, [], json_encode($jwks, JSON_THROW_ON_ERROR))]);
        $http = new Client(['handler' => HandlerStack::create($mock)]);

        /** @var AbstractPdo&\PHPUnit\Framework\MockObject\MockObject $db */
        $db = $this->createMock(AbstractPdo::class);
        /** @var AuditLogger&\PHPUnit\Framework\MockObject\MockObject $auditLogger */
        $auditLogger = $this->createMock(AuditLogger::class);

        return new AuthService($jwksUrl, self::ISSUER, self::AUDIENCE, $db, $auditLogger, $http);
    }

    /**
     * @param array<string, mixed> $claims
     */
    private function issueToken(OpenSSLAsymmetricKey $privateKey, array $claims): string
    {
        return JWT::encode($claims, $privateKey, 'RS256', 'test-key-1');
    }

    private function base64UrlEncode(string $binary): string
    {
        return rtrim(strtr(base64_encode($binary), '+/', '-_'), '=');
    }
}
