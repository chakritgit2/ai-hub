<?php

declare(strict_types=1);

namespace ConsoleApi\Services;

use GuzzleHttp\Client;
use GuzzleHttp\ClientInterface;
use GuzzleHttp\Exception\GuzzleException;

/**
 * Guzzle wrapper for console-api -> Grafana Tempo's query API (PRD §6.9 Trace tab
 * waterfall), used by RunsController::getRunTrace().
 *
 * Unlike RuntimeClient, this carries no internal JWT - Tempo's query API has no auth
 * concept at all in this deployment (deploy/k8s/base/tempo-deployment.yaml: no auth
 * sidecar, no token config). Access control is the NetworkPolicy restricting who can
 * reach Tempo's Service, plus RunsController's own requireMembership() check on the
 * *run* a trace is being fetched for - never a credential Tempo itself checks.
 */
class TempoClient
{
    private ClientInterface $http;

    public function __construct(string $baseUrl, int $timeoutSeconds, ?ClientInterface $http = null)
    {
        $this->http = $http ?? new Client([
            'base_uri' => rtrim($baseUrl, '/') . '/',
            'timeout' => $timeoutSeconds,
        ]);
    }

    /**
     * GET /api/traces/{traceId} - the raw OTLP-shaped trace response (resourceSpans/
     * batches, see TempoTraceFlattener for the shape this gets turned into).
     *
     * Returns null on ANY failure - transport exception (Tempo unreachable, timeout),
     * non-200 (404 for an unknown/purged trace id, 5xx), or a non-JSON-object body. Every
     * one of these is the same outcome to RunsController: "no trace available for this
     * run" - it never needs to distinguish "Tempo is down" from "this trace aged out of
     * the 14-day retention" from "tracing was never on for this run", all three render
     * identically.
     *
     * @return array<string, mixed>|null
     */
    public function getTrace(string $traceId): ?array
    {
        try {
            $response = $this->http->request('GET', "api/traces/{$traceId}", ['http_errors' => false]);
        } catch (GuzzleException) {
            return null;
        }

        if ($response->getStatusCode() !== 200) {
            return null;
        }

        $decoded = json_decode((string) $response->getBody(), true);

        return is_array($decoded) ? $decoded : null;
    }
}
