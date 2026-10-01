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
 *
 * `/internal/v1/*` itself is still a 501 stub on the ai-runtime side (the compiler isn't
 * built yet) — this class only owns the transport, not compile logic, so it doesn't need
 * to change once that lands.
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
     * used by AgentsController::publishAgentVersion() (PRD §4.4-A). Not called from there
     * yet — AgentsController is still its own 501 stub.
     *
     * @param array<string, mixed> $spec
     * @return array<string, mixed>
     */
    public function compileAgentSpec(array $spec, string $role): array
    {
        return $this->post('agents/compile', ['spec' => $spec, 'role' => $role]);
    }

    /**
     * @param array<string, mixed> $body
     * @return array<string, mixed>
     */
    private function post(string $path, array $body): array
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
            $response = $this->http->post($path, ['headers' => $headers, 'json' => $body]);
        } catch (RequestException $exception) {
            // A non-2xx here means ai-runtime itself failed (including its current 501
            // stub) — a compile *failure* is still a 200 with ok:false per the contract,
            // so this is always a transport/service problem, never a bad spec.
            $detail = $exception->hasResponse()
                ? (string) $exception->getResponse()->getBody()
                : $exception->getMessage();

            throw new RuntimeException("RuntimeClient: POST {$path} failed: {$detail}", 0, $exception);
        } catch (GuzzleException $exception) {
            throw new RuntimeException(
                "RuntimeClient: POST {$path} failed: " . $exception->getMessage(),
                0,
                $exception
            );
        }

        $decoded = json_decode((string) $response->getBody(), true);

        return is_array($decoded) ? $decoded : [];
    }
}
