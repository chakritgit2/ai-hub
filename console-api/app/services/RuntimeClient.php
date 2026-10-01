<?php

declare(strict_types=1);

namespace ConsoleApi\Services;

use ConsoleApi\Middleware\TraceparentMiddleware;
use GuzzleHttp\Client;
use GuzzleHttp\ClientInterface;
use GuzzleHttp\Exception\GuzzleException;
use GuzzleHttp\Exception\RequestException;
use RuntimeException;

/**
 * Guzzle wrapper for console-api -> ai-runtime `/internal/v1/*` calls (PRD §9.2).
 *
 * Every call signs a 60-second internal JWT (`aud=ai-internal`, TokenIssuer::issueInternalToken())
 * and sets `X-Company-Id` from the current request's CompanyContext plus the `traceparent`
 * captured by TraceparentMiddleware, so traces span both services (PRD §6.9, §7.6).
 */
class RuntimeClient
{
    private ClientInterface $http;

    public function __construct(
        string $baseUrl,
        int $timeoutSeconds,
        private readonly TokenIssuer $tokenIssuer,
        private readonly CompanyContext $companyContext,
        ?ClientInterface $http = null
    ) {
        // ai-internal.yaml declares `servers: - url: /internal/v1` — every operation path
        // (e.g. `/agents/compile`) is relative to that, not to the bare host in
        // AI_RUNTIME_INTERNAL_BASE_URL.
        $this->http = $http ?? new Client([
            'base_uri' => rtrim($baseUrl, '/') . '/internal/v1/',
            'timeout' => $timeoutSeconds,
        ]);
    }

    /**
     * POST /internal/v1/agents/compile — compiles an agent spec into a runnable definition
     * used by AgentsController::publishAgentVersion() (PRD §4.4-A).
     *
     * @param array<string, mixed> $spec
     * @return array<string, mixed>
     */
    public function compileAgentSpec(array $spec, string $role): array
    {
        return $this->request('POST', 'agents/compile', ['spec' => $spec, 'role' => $role]);
    }

    /**
     * PUT /internal/v1/connections/{id}/secret — envelope-encrypts the secret with the
     * company's own DEK and stores it, used by ConnectionsController::putConnectionSecret()
     * (PRD §7.4/§7.7). console-api itself never persists the plaintext secret.
     */
    public function putConnectionSecret(string $connectionId, string $secret): void
    {
        $this->request('PUT', "connections/{$connectionId}/secret", ['secret' => $secret]);
    }

    /**
     * POST /internal/v1/connections/{id}/test — makes a real, cheap call to the provider
     * to confirm the stored secret actually works, used by ConnectionsController::testConnection().
     *
     * @return array<string, mixed>
     */
    public function testConnection(string $connectionId): array
    {
        return $this->request('POST', "connections/{$connectionId}/test", []);
    }

    /**
     * @param array<string, mixed> $body
     * @return array<string, mixed>
     */
    private function request(string $method, string $path, array $body): array
    {
        if (!$this->companyContext->hasCompanyId()) {
            throw new RuntimeException('RuntimeClient: no company_id bound for this request.');
        }

        $headers = [
            'Authorization' => 'Bearer ' . $this->tokenIssuer->issueInternalToken(),
            'X-Company-Id' => $this->companyContext->getCompanyId(),
        ];

        $traceparent = TraceparentMiddleware::current();
        if ($traceparent !== null) {
            $headers['traceparent'] = $traceparent;
        }

        try {
            $response = $this->http->request($method, $path, ['headers' => $headers, 'json' => $body]);
        } catch (RequestException $exception) {
            // A non-2xx here means ai-runtime itself failed — a compile/test *failure* is
            // still a 200 with ok:false per the contract, so this is always a
            // transport/service problem, never a bad spec or a bad secret.
            $detail = $exception->hasResponse()
                ? (string) $exception->getResponse()->getBody()
                : $exception->getMessage();

            throw new RuntimeException("RuntimeClient: {$method} {$path} failed: {$detail}", 0, $exception);
        } catch (GuzzleException $exception) {
            throw new RuntimeException(
                "RuntimeClient: {$method} {$path} failed: " . $exception->getMessage(),
                0,
                $exception
            );
        }

        $decoded = json_decode((string) $response->getBody(), true);

        return is_array($decoded) ? $decoded : [];
    }
}
