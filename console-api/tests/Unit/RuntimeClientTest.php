<?php

declare(strict_types=1);

namespace ConsoleApi\Tests\Unit;

use ConsoleApi\Services\CompanyContext;
use ConsoleApi\Services\RuntimeClient;
use ConsoleApi\Services\TokenIssuer;
use Firebase\JWT\JWT;
use GuzzleHttp\Client;
use GuzzleHttp\Exception\ServerException;
use GuzzleHttp\Handler\MockHandler;
use GuzzleHttp\HandlerStack;
use GuzzleHttp\Psr7\Request;
use GuzzleHttp\Psr7\Response;
use PHPUnit\Framework\TestCase;
use RuntimeException;

/**
 * Exercises RuntimeClient's transport — signed internal JWT, X-Company-Id, body —
 * against a mocked HTTP handler (no real ai-runtime needed, no network).
 */
final class RuntimeClientTest extends TestCase
{
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

    public function testCompileAgentSpecSendsSignedInternalTokenAndCompanyHeader(): void
    {
        /** @var Request|null $capturedRequest */
        $capturedRequest = null;
        $history = static function ($request) use (&$capturedRequest): void {
            $capturedRequest = $request;
        };

        $mock = new MockHandler([new Response(200, [], json_encode(['ok' => true, 'compiler_version' => 'c1']))]);
        $stack = HandlerStack::create($mock);
        $stack->push(\GuzzleHttp\Middleware::tap($history));
        $http = new Client(['handler' => $stack, 'base_uri' => 'http://ai-runtime.invalid/internal/v1/']);

        $client = $this->makeRuntimeClient($http, 'company-1');

        $result = $client->compileAgentSpec(['identity' => ['name' => 'x']], 'developer');

        self::assertSame(['ok' => true, 'compiler_version' => 'c1'], $result);

        self::assertNotNull($capturedRequest);
        self::assertSame('/internal/v1/agents/compile', $capturedRequest->getUri()->getPath());
        self::assertSame('company-1', $capturedRequest->getHeaderLine('X-Company-Id'));

        $authHeader = $capturedRequest->getHeaderLine('Authorization');
        self::assertStringStartsWith('Bearer ', $authHeader);
        $token = substr($authHeader, 7);

        $publicKey = openssl_pkey_get_details(openssl_pkey_get_private(file_get_contents($this->keyPath)))['key'];
        $payload = JWT::decode($token, new \Firebase\JWT\Key($publicKey, 'RS256'));
        self::assertSame('ai-internal', $payload->aud);
        self::assertSame('console-api', $payload->iss);

        $body = json_decode((string) $capturedRequest->getBody(), true);
        self::assertSame('developer', $body['role']);
        self::assertSame(['name' => 'x'], $body['spec']['identity']);
    }

    public function testPutConnectionSecretSendsSignedTokenAndSecretBody(): void
    {
        /** @var Request|null $capturedRequest */
        $capturedRequest = null;
        $history = static function ($request) use (&$capturedRequest): void {
            $capturedRequest = $request;
        };

        $mock = new MockHandler([new Response(204)]);
        $stack = HandlerStack::create($mock);
        $stack->push(\GuzzleHttp\Middleware::tap($history));
        $http = new Client(['handler' => $stack, 'base_uri' => 'http://ai-runtime.invalid/internal/v1/']);

        $client = $this->makeRuntimeClient($http, 'company-1');

        $client->putConnectionSecret('11111111-1111-1111-1111-111111111111', 'sk-super-secret');

        self::assertNotNull($capturedRequest);
        self::assertSame('PUT', $capturedRequest->getMethod());
        self::assertSame(
            '/internal/v1/connections/11111111-1111-1111-1111-111111111111/secret',
            $capturedRequest->getUri()->getPath()
        );
        self::assertSame('company-1', $capturedRequest->getHeaderLine('X-Company-Id'));
        self::assertStringStartsWith('Bearer ', $capturedRequest->getHeaderLine('Authorization'));

        $body = json_decode((string) $capturedRequest->getBody(), true);
        self::assertSame('sk-super-secret', $body['secret']);
    }

    public function testTestConnectionSendsSignedTokenAndReturnsDecodedResult(): void
    {
        /** @var Request|null $capturedRequest */
        $capturedRequest = null;
        $history = static function ($request) use (&$capturedRequest): void {
            $capturedRequest = $request;
        };

        $mock = new MockHandler([new Response(200, [], json_encode(['ok' => true]))]);
        $stack = HandlerStack::create($mock);
        $stack->push(\GuzzleHttp\Middleware::tap($history));
        $http = new Client(['handler' => $stack, 'base_uri' => 'http://ai-runtime.invalid/internal/v1/']);

        $client = $this->makeRuntimeClient($http, 'company-1');

        $result = $client->testConnection('11111111-1111-1111-1111-111111111111');

        self::assertSame(['ok' => true], $result);
        self::assertNotNull($capturedRequest);
        self::assertSame('POST', $capturedRequest->getMethod());
        self::assertSame(
            '/internal/v1/connections/11111111-1111-1111-1111-111111111111/test',
            $capturedRequest->getUri()->getPath()
        );
        self::assertSame('company-1', $capturedRequest->getHeaderLine('X-Company-Id'));
    }

    public function testCompileAgentSpecWithoutCompanyContextThrows(): void
    {
        $mock = new MockHandler([new Response(200, [], '{}')]);
        $http = new Client(['handler' => HandlerStack::create($mock)]);

        $client = $this->makeRuntimeClient($http, null);

        $this->expectException(RuntimeException::class);
        $client->compileAgentSpec([], 'developer');
    }

    public function testNon2xxResponseIsWrappedWithBodyDetail(): void
    {
        $mock = new MockHandler([
            new ServerException(
                'Server error',
                new Request('POST', 'agents/compile'),
                new Response(501, [], json_encode(['error' => 'not_implemented']))
            ),
        ]);
        $http = new Client(['handler' => HandlerStack::create($mock)]);

        $client = $this->makeRuntimeClient($http, 'company-1');

        try {
            $client->compileAgentSpec([], 'developer');
            self::fail('Expected a RuntimeException');
        } catch (RuntimeException $exception) {
            self::assertStringContainsString('not_implemented', $exception->getMessage());
        }
    }

    private function makeRuntimeClient($http, ?string $companyId): RuntimeClient
    {
        $tokenIssuer = new TokenIssuer($this->keyPath, 'test-kid', 'console-api', 'ai-runtime', 'ai-internal');
        $companyContext = new CompanyContext();
        if ($companyId !== null) {
            $companyContext->setCompanyId($companyId);
        }

        return new RuntimeClient('http://ai-runtime.invalid/internal/v1/', 10, $tokenIssuer, $companyContext, $http);
    }
}
