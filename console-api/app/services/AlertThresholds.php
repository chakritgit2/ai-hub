<?php

declare(strict_types=1);

namespace ConsoleApi\Services;

use Phalcon\Db\Adapter\Pdo\AbstractPdo;
use Phalcon\Db\Enum;

/**
 * Per-company tunables for the Dashboard's alert banner (`console.alert_thresholds`,
 * added 2026-11-06 alongside the in-console Dashboard that replaces the external Grafana
 * dashboard) - shared between SettingsController (read/write the company's own row) and
 * DashboardController (read-only, to evaluate the 4 alert conditions), so both can't
 * drift out of sync on what a "default" threshold actually is.
 */
final class AlertThresholds
{
    public const DEFAULTS = [
        'quota_pct' => 80.0,
        'error_rate_multiplier' => 2.0,
        'error_rate_min_errors' => 3,
        'error_rate_min_pct' => 10.0,
        'guardrail_multiplier' => 3.0,
        'guardrail_min_count' => 5,
        'latency_multiplier' => 2.0,
        'latency_min_ms' => 2000,
        'cost_multiplier' => 2.0,
        'cost_min_usd' => 0.01,
    ];

    /**
     * Reads the current company's own row (RLS-scoped - caller must already be inside a
     * runInCompanyTransaction()), falling back to DEFAULTS when the company has never
     * customized its thresholds (no row exists yet - see the Phinx migration's own
     * "created lazily on first PUT" note).
     *
     * @return array<string, int|float>
     */
    public static function resolve(AbstractPdo $db): array
    {
        $row = $db->fetchOne('SELECT * FROM console.alert_thresholds LIMIT 1', Enum::FETCH_ASSOC);

        return $row !== false ? self::format($row) : self::DEFAULTS;
    }

    /**
     * @param array<string, mixed> $row
     * @return array<string, int|float>
     */
    public static function format(array $row): array
    {
        return [
            'quota_pct' => (float) $row['quota_pct'],
            'error_rate_multiplier' => (float) $row['error_rate_multiplier'],
            'error_rate_min_errors' => (int) $row['error_rate_min_errors'],
            'error_rate_min_pct' => (float) $row['error_rate_min_pct'],
            'guardrail_multiplier' => (float) $row['guardrail_multiplier'],
            'guardrail_min_count' => (int) $row['guardrail_min_count'],
            'latency_multiplier' => (float) $row['latency_multiplier'],
            'latency_min_ms' => (int) $row['latency_min_ms'],
            'cost_multiplier' => (float) $row['cost_multiplier'],
            'cost_min_usd' => (float) $row['cost_min_usd'],
        ];
    }
}
