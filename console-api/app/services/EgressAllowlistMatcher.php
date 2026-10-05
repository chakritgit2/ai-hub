<?php

declare(strict_types=1);

namespace ConsoleApi\Services;

/**
 * Pure host-pattern matching against a company's `console.egress_allowlist` rows (PRD §7.4).
 *
 * This is deliberately the *save-time* half of the check only — host pattern match, no DNS
 * resolution, no private-IP detection, no network access. That half lives in ai-runtime's
 * `SafeHttpClient` (ai/app/integrations/safe_http_client.py), which re-validates the
 * resolved IP at request time. A connection's `api_base` must pass both layers.
 */
final class EgressAllowlistMatcher
{
    /**
     * $port is nullable for callers that genuinely don't know the target port (none
     * today — every caller should pass it); a row's own `port` only narrows the match
     * when both are known, so an unknown $port degrades to host-only matching rather
     * than rejecting every port-scoped row outright.
     *
     * @param array<int, array{host_pattern: string, port?: int|null, allow_private_ip?: bool}> $rows
     */
    public static function hostAllowed(array $rows, string $host, ?int $port = null): bool
    {
        $host = strtolower($host);

        foreach ($rows as $row) {
            $rowPort = $row['port'] ?? null;
            // SafeHttpClient's _matching_entries (ai/app/integrations/safe_http_client.py)
            // applies the same "row port null means any port" rule at request time — this
            // save-time check must agree, or a config that passes here can still 422 when
            // actually tested/run.
            if ($rowPort !== null && $port !== null && $rowPort !== $port) {
                continue;
            }

            $pattern = strtolower((string) $row['host_pattern']);

            if ($pattern === $host) {
                return true;
            }

            if (str_starts_with($pattern, '*.')) {
                $suffix = substr($pattern, 1); // keep the leading dot, e.g. ".example.com"
                if ($host !== ltrim($suffix, '.') && str_ends_with($host, $suffix)) {
                    return true;
                }
            }
        }

        return false;
    }

    /**
     * Mirrors ai/app/integrations/safe_http_client.py's `_DEFAULT_PORTS` — a URL with no
     * explicit port still has a real target port once a scheme is involved, and that's
     * what a port-scoped allowlist row must be compared against.
     */
    public static function defaultPortForScheme(?string $scheme): int
    {
        return strtolower((string) $scheme) === 'http' ? 80 : 443;
    }
}
