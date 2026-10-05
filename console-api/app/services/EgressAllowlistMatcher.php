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
     * @param array<int, array{host_pattern: string, port?: int|null, allow_private_ip?: bool}> $rows
     */
    public static function hostAllowed(array $rows, string $host): bool
    {
        $host = strtolower($host);

        foreach ($rows as $row) {
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
}
