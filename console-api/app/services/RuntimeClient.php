<?php

declare(strict_types=1);

namespace ConsoleApi\Services;

use GuzzleHttp\Client;
use RuntimeException;

/**
 * Guzzle wrapper for console-api -> ai-runtime `/internal/v1/*` calls.
 *
 * The real implementation signs a 60-second internal JWT (`aud=ai-internal`), and sets
 * `X-Company-Id` plus the `traceparent` header captured by TraceparentMiddleware so
 * traces span both services (PRD §6.9, §7.6). Not implemented yet — every method throws.
 */
class RuntimeClient
{
    private Client $http;

    public function __construct(private readonly string $baseUrl, int $timeoutSeconds = 10)
    {
        $this->http = new Client([
            'base_uri' => rtrim($baseUrl, '/') . '/',
            'timeout' => $timeoutSeconds,
        ]);
    }

    /**
     * POST /internal/v1/compile — compiles an agent spec into a runnable definition
     * used by AgentsController::publishAgentVersion() (PRD §4.4-A).
     *
     * @param array<string, mixed> $spec
     * @return array<string, mixed>
     */
    public function compileAgentSpec(array $spec, string $role): array
    {
        throw new RuntimeException('not_implemented: RuntimeClient::compileAgentSpec (PRD §4.4-A/§7.6)');
    }
}
