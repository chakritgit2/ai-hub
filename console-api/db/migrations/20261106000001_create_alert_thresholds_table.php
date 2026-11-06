<?php

declare(strict_types=1);

use Phinx\Migration\AbstractMigration;

/**
 * Per-company tunables for the Dashboard's alert banner (PRD-adjacent - added 2026-11-06
 * alongside the in-console Dashboard that replaces the external Grafana dashboard). At
 * most one row per company, created lazily on first PUT - a company that never visits
 * Settings' Alerts tab simply has no row, and the built-in defaults apply (see
 * DashboardController::DEFAULT_ALERT_THRESHOLDS).
 */
final class CreateAlertThresholdsTable extends AbstractMigration
{
    public function up(): void
    {
        $this->execute(<<<SQL
            CREATE TABLE console.alert_thresholds (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                company_id uuid NOT NULL UNIQUE,
                quota_pct numeric NOT NULL DEFAULT 80,
                error_rate_multiplier numeric NOT NULL DEFAULT 2,
                error_rate_min_errors integer NOT NULL DEFAULT 3,
                error_rate_min_pct numeric NOT NULL DEFAULT 10,
                guardrail_multiplier numeric NOT NULL DEFAULT 3,
                guardrail_min_count integer NOT NULL DEFAULT 5,
                latency_multiplier numeric NOT NULL DEFAULT 2,
                latency_min_ms integer NOT NULL DEFAULT 2000,
                cost_multiplier numeric NOT NULL DEFAULT 2,
                cost_min_usd numeric NOT NULL DEFAULT 0.01,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        SQL);
    }

    public function down(): void
    {
        $this->execute('DROP TABLE IF EXISTS console.alert_thresholds');
    }
}
