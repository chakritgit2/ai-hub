<?php

declare(strict_types=1);

namespace ConsoleApi\Tests\Unit;

use ConsoleApi\Services\TempoClient;
use GuzzleHttp\Client;
use GuzzleHttp\Exception\ConnectException;
use GuzzleHttp\Handler\MockHandler;
use GuzzleHttp\HandlerStack;
use GuzzleHttp\Psr7\Request;
use GuzzleHttp\Psr7\Response;
use PHPUnit\Framework\TestCase;

/**
 * Exercises TempoClient's transport - no JWT, no X-Company-Id (unlike RuntimeClient,
 * Tempo's query API has no auth concept) - and its "every failure mode collapses to
 * null" contract, against a mocked HTTP handler (no real Tempo needed, no network).
 */
final class TempoClientTest extends TestCase
{
    public function testGetTraceReturnsDecodedBodyOn200(): void
    {
        /** @var Request|null $capturedRequest */
        $capturedRequest = null;
        $history = static function ($request) use (&$capturedRequest): void {
            $capturedRequest = $request;
        };

        $mock = new MockHandler([
            new Response(200, [], json_encode(['resourceSpans' => []])),
        ]);
        $stack = HandlerStack::create($mock);
        $stack->push(\GuzzleHttp\Middleware::tap($history));
        $http = new Client(['handler' => $stack, 'base_uri' => 'http://tempo.invalid/']);

        $client = new TempoClient('http://tempo.invalid', 5, $http);

        $result = $client->getTrace('1234567890abcdef1234567890abcdef');

        self::assertSame(['resourceSpans' => []], $result);
        self::assertNotNull($capturedRequest);
        self::assertSame('GET', $capturedRequest->getMethod());
        self::assertSame(
            '/api/traces/1234567890abcdef1234567890abcdef',
            $capturedRequest->getUri()->getPath()
        );
        self::assertSame('', $capturedRequest->getHeaderLine('Authorization'));
    }

    public function testGetTraceReturnsNullOn404(): void
    {
        $mock = new MockHandler([new Response(404, [], 'trace not found')]);
        $http = new Client(['handler' => HandlerStack::create($mock), 'base_uri' => 'http://tempo.invalid/']);

        $client = new TempoClient('http://tempo.invalid', 5, $http);

        self::assertNull($client->getTrace('deadbeef'));
    }

    public function testGetTraceReturnsNullOn500(): void
    {
        $mock = new MockHandler([new Response(500, [], 'internal error')]);
        $http = new Client(['handler' => HandlerStack::create($mock), 'base_uri' => 'http://tempo.invalid/']);

        $client = new TempoClient('http://tempo.invalid', 5, $http);

        self::assertNull($client->getTrace('deadbeef'));
    }

    public function testGetTraceReturnsNullOnTransportException(): void
    {
        $mock = new MockHandler([
            new ConnectException('connection refused', new Request('GET', 'http://tempo.invalid/')),
        ]);
        $http = new Client(['handler' => HandlerStack::create($mock), 'base_uri' => 'http://tempo.invalid/']);

        $client = new TempoClient('http://tempo.invalid', 5, $http);

        self::assertNull($client->getTrace('deadbeef'));
    }

    public function testGetTraceReturnsNullOnNonJsonBody(): void
    {
        $mock = new MockHandler([new Response(200, [], 'not json')]);
        $http = new Client(['handler' => HandlerStack::create($mock), 'base_uri' => 'http://tempo.invalid/']);

        $client = new TempoClient('http://tempo.invalid', 5, $http);

        self::assertNull($client->getTrace('deadbeef'));
    }
}
