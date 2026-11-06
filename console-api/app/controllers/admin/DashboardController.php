<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use ConsoleApi\Services\AlertThresholds;
use Phalcon\Db\Enum;
use Phalcon\Http\Response;

/**
 * /dashboard — aggregate usage/cost summary for the current company (PRD §6.9
 * `DashboardSummary`), computed from `logs.runs`/`logs.guardrail_events`. Also the
 * in-console replacement for the external Grafana dashboard (`deploy/grafana/
 * dynamiq-runs-overview.json`) - that JSON was prepared for infra to import into the
 * cluster's shared Grafana but was never confirmed live there (this session has no
 * access to the `monitoring` namespace to check), and the product direction as of
 * 2026-11-06 is to not depend on that at all - every figure a user needs lives in this
 * one page instead, computed directly from Postgres.
 *
 * `runs_today`/`error_rate`/`guardrail_triggers` use a "today" window;
 * `tokens_this_month`/`cost_this_month_usd` use a "this month" window (per field name) -
 * both bounded by Asia/Bangkok wall-clock time, matching the quota-reset convention
 * PRD §6.3 uses elsewhere ("reset at midnight Asia/Bangkok"). The five trend/breakdown
 * series below (`runs_per_day`, `latency_per_day`, `tokens_cost_per_day`,
 * `guardrail_actions_per_day`, `top_agents`) all use the same 14-day Bangkok-aligned
 * window as `runs_per_day` always has, for one consistent "last 14 days" story across
 * the whole page rather than a mix of windows a reader has to track separately.
 */
class DashboardController extends ControllerBase
{
    public function getDashboardSummary(): Response
    {
        if (($error = $this->requireCompanyId()) !== null) {
            return $error;
        }
        if (($error = $this->requireMembership()) !== null) {
            return $error;
        }

        // latency_p95_today/cost_today exist only to feed the alert-banner spike checks
        // below (computeAlerts()) - not otherwise surfaced as their own tile, so they
        // ride along on this same query rather than a separate round trip.
        $runs = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchOne(
                "SELECT
                    count(*) FILTER (WHERE created_at >= date_trunc('day', now() AT TIME ZONE 'Asia/Bangkok') AT TIME ZONE 'Asia/Bangkok') AS runs_today,
                    count(*) FILTER (WHERE status = 'error' AND created_at >= date_trunc('day', now() AT TIME ZONE 'Asia/Bangkok') AT TIME ZONE 'Asia/Bangkok') AS error_runs_today,
                    coalesce(sum(tokens_in) FILTER (WHERE created_at >= date_trunc('month', now() AT TIME ZONE 'Asia/Bangkok') AT TIME ZONE 'Asia/Bangkok'), 0) AS tokens_in_month,
                    coalesce(sum(tokens_out) FILTER (WHERE created_at >= date_trunc('month', now() AT TIME ZONE 'Asia/Bangkok') AT TIME ZONE 'Asia/Bangkok'), 0) AS tokens_out_month,
                    coalesce(sum(cost_usd) FILTER (WHERE created_at >= date_trunc('month', now() AT TIME ZONE 'Asia/Bangkok') AT TIME ZONE 'Asia/Bangkok'), 0) AS cost_month,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
                        FILTER (WHERE created_at >= date_trunc('day', now() AT TIME ZONE 'Asia/Bangkok') AT TIME ZONE 'Asia/Bangkok' AND latency_ms IS NOT NULL) AS latency_p95_today,
                    coalesce(sum(cost_usd) FILTER (WHERE created_at >= date_trunc('day', now() AT TIME ZONE 'Asia/Bangkok') AT TIME ZONE 'Asia/Bangkok'), 0) AS cost_today
                 FROM logs.runs",
                Enum::FETCH_ASSOC
            )
        );

        $guardrailTriggers = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchOne(
                "SELECT count(*) AS n FROM logs.guardrail_events
                 WHERE created_at >= date_trunc('day', now() AT TIME ZONE 'Asia/Bangkok') AT TIME ZONE 'Asia/Bangkok'",
                Enum::FETCH_ASSOC
            )
        );

        // Both needed only for the alert banner (computeAlerts()): the company's own
        // monthly_budget_usd (console.companies has no RLS policy - see that table's own
        // comment in 002_rls_policies.sql - so this is a plain WHERE id = :id read, not a
        // leak) and this company's own alert_thresholds row, defaulting via
        // AlertThresholds::resolve() when it has never customized them.
        $companyBudget = $this->runInCompanyTransaction(
            fn ($db) => $db->fetchOne(
                'SELECT monthly_budget_usd FROM console.companies WHERE id = :id',
                Enum::FETCH_ASSOC,
                ['id' => $this->getCompanyId()]
            )
        );
        $thresholds = $this->runInCompanyTransaction(static fn ($db) => AlertThresholds::resolve($db));

        // Last 14 full days (Bangkok-aligned), *excluding* today - today is still
        // in-progress and would otherwise show as a misleadingly short last bar/point on
        // every trend chart below. `runs_today`/`error_rate` tiles above already cover
        // today on their own terms; the 14-day trend series are deliberately a settled,
        // comparable-day-over-day window instead.
        // `generate_series` is inclusive on BOTH bounds, so 14 full days ending
        // yesterday needs start = today-14 and end = today-1 (not today) - today-14 to
        // today would generate 15 rows and still include today's partial day, exactly
        // the off-by-one this window is here to avoid.
        //
        // Wrapped in its own parens - found live, via a real query against the dev DB,
        // that without them `{$windowStart} AT TIME ZONE 'Asia/Bangkok'` (used below in
        // topAgents) parses as `date_trunc(...) - (interval '14 days' AT TIME ZONE
        // 'Asia/Bangkok')`, not `(date_trunc(...) - interval '14 days') AT TIME ZONE
        // 'Asia/Bangkok'` - Postgres's AT TIME ZONE binds tighter than the preceding `-`,
        // so it grabbed the interval literal instead of the whole expression
        // (`function pg_catalog.timezone(unknown, interval) does not exist`).
        $windowStart = "(date_trunc('day', now() AT TIME ZONE 'Asia/Bangkok') - interval '14 days')";
        $windowEnd = "(date_trunc('day', now() AT TIME ZONE 'Asia/Bangkok') - interval '1 day')";

        // `generate_series` first, `logs.runs` LEFT JOINed onto it, so every one of the 14
        // days appears even with zero runs that day - a plain `GROUP BY` on `logs.runs`
        // alone would silently drop days with no rows instead of reporting them as 0,
        // which would misrender as a gap in the chart rather than a real quiet day.
        //
        // The join condition is a sargable range on the bare `r.created_at` column
        // (`d.day` converted to a real UTC instant via `AT TIME ZONE`, not the other way
        // around) rather than `date_trunc(r.created_at AT TIME ZONE ...) = d.day` - the
        // latter wraps the column itself in a function, defeating any index on
        // created_at and forcing a full scan of the company's entire run history on
        // every dashboard load. Same bare-column convention the runs_today/tokens_this_month
        // queries above already use, and every query below reuses.
        //
        // Status broken out by FILTER rather than a plain GROUP BY status - a reader
        // wants "how many of each, same day, same bar" (a stacked chart), not a flat list
        // that would need client-side pivoting. `other` absorbs interrupted/truncated/
        // cancelled - rare enough, and distinct enough from "error"/"blocked" in meaning,
        // that folding them into "error" would misrepresent why a run didn't finish
        // cleanly, but they're not common enough individually to earn their own series
        // (dataviz skill: a 9th-ish rare series folds into "Other" rather than growing
        // the legend).
        $runsPerDay = $this->runInCompanyTransaction(
            fn ($db) => $db->fetchAll(
                "SELECT to_char(d.day, 'YYYY-MM-DD') AS date,
                        count(r.id) FILTER (WHERE r.status = 'success') AS success,
                        count(r.id) FILTER (WHERE r.status = 'error') AS error,
                        count(r.id) FILTER (WHERE r.status = 'blocked') AS blocked,
                        count(r.id) FILTER (WHERE r.status NOT IN ('success', 'error', 'blocked')) AS other
                 FROM generate_series({$windowStart}, {$windowEnd}, interval '1 day') AS d(day)
                 LEFT JOIN logs.runs r
                   ON r.created_at >= (d.day AT TIME ZONE 'Asia/Bangkok')
                  AND r.created_at < ((d.day + interval '1 day') AT TIME ZONE 'Asia/Bangkok')
                 GROUP BY d.day
                 ORDER BY d.day",
                Enum::FETCH_ASSOC
            )
        );

        // p50/p95/p99 per day, not just one overall figure - a single spike day is
        // exactly what "is this normal" needs to see, and gets averaged away by an
        // overall percentile. Matches the 3 percentiles the external Grafana dashboard
        // (deploy/grafana/dynamiq-runs-overview.json, no longer used - PRD direction as
        // of 2026-11-06) showed, so this panel is a real like-for-like replacement, not
        // a reduced one. `latency_ms` is NULL for a run that never reached the LLM
        // (blocked at input guardrail) - excluded via the WHERE, same reasoning
        // RunsController's own latency display already uses, not a new judgment call.
        $latencyPerDay = $this->runInCompanyTransaction(
            fn ($db) => $db->fetchAll(
                "SELECT to_char(d.day, 'YYYY-MM-DD') AS date,
                        percentile_cont(0.5) WITHIN GROUP (ORDER BY r.latency_ms) AS p50,
                        percentile_cont(0.95) WITHIN GROUP (ORDER BY r.latency_ms) AS p95,
                        percentile_cont(0.99) WITHIN GROUP (ORDER BY r.latency_ms) AS p99
                 FROM generate_series({$windowStart}, {$windowEnd}, interval '1 day') AS d(day)
                 LEFT JOIN logs.runs r
                   ON r.created_at >= (d.day AT TIME ZONE 'Asia/Bangkok')
                  AND r.created_at < ((d.day + interval '1 day') AT TIME ZONE 'Asia/Bangkok')
                  AND r.latency_ms IS NOT NULL
                 GROUP BY d.day
                 ORDER BY d.day",
                Enum::FETCH_ASSOC
            )
        );

        $tokensCostPerDay = $this->runInCompanyTransaction(
            fn ($db) => $db->fetchAll(
                "SELECT to_char(d.day, 'YYYY-MM-DD') AS date,
                        coalesce(sum(r.tokens_in), 0) AS tokens_in,
                        coalesce(sum(r.tokens_out), 0) AS tokens_out,
                        coalesce(sum(r.cost_usd), 0) AS cost_usd
                 FROM generate_series({$windowStart}, {$windowEnd}, interval '1 day') AS d(day)
                 LEFT JOIN logs.runs r
                   ON r.created_at >= (d.day AT TIME ZONE 'Asia/Bangkok')
                  AND r.created_at < ((d.day + interval '1 day') AT TIME ZONE 'Asia/Bangkok')
                 GROUP BY d.day
                 ORDER BY d.day",
                Enum::FETCH_ASSOC
            )
        );

        // `logs.guardrail_events` has no status/day gaps to fill the way logs.runs does
        // above - every row here already IS a triggered check (PRD §6.4: pass isn't
        // logged at all, see log_guardrail_events), so a day with zero triggers
        // legitimately has zero rows, not a hidden one. Still generate_series'd for the
        // same reason as every other series here: a quiet day must render as a real zero
        // bar, not a gap the reader has to guess the meaning of.
        $guardrailActionsPerDay = $this->runInCompanyTransaction(
            fn ($db) => $db->fetchAll(
                "SELECT to_char(d.day, 'YYYY-MM-DD') AS date,
                        count(ge.id) FILTER (WHERE ge.action = 'block') AS block,
                        count(ge.id) FILTER (WHERE ge.action = 'mask') AS mask,
                        count(ge.id) FILTER (WHERE ge.action = 'flag') AS flag
                 FROM generate_series({$windowStart}, {$windowEnd}, interval '1 day') AS d(day)
                 LEFT JOIN logs.guardrail_events ge
                   ON ge.created_at >= (d.day AT TIME ZONE 'Asia/Bangkok')
                  AND ge.created_at < ((d.day + interval '1 day') AT TIME ZONE 'Asia/Bangkok')
                 GROUP BY d.day
                 ORDER BY d.day",
                Enum::FETCH_ASSOC
            )
        );

        // COALESCE(r.agent_name, w.name) rather than a bare GROUP BY agent_name - a
        // workflow run has agent_name NULL (RunsController::RUN_COLUMNS's own docblock
        // notes this), so without the fallback every workflow run would collapse into one
        // misleading NULL/"Unknown" row instead of being broken out by workflow like an
        // agent run is by agent. Same `console.workflow_versions`/`console.workflows`
        // LEFT JOIN RunsController::RUN_FROM already uses for the same reason.
        $topAgents = $this->runInCompanyTransaction(
            fn ($db) => $db->fetchAll(
                "SELECT coalesce(r.agent_name, w.name, 'Unknown') AS name,
                        count(r.id) AS run_count,
                        coalesce(sum(r.cost_usd), 0) AS cost_usd
                 FROM logs.runs r
                 LEFT JOIN console.workflow_versions wv ON wv.id = r.workflow_version_id
                 LEFT JOIN console.workflows w ON w.id = wv.workflow_id
                 WHERE r.created_at >= ({$windowStart} AT TIME ZONE 'Asia/Bangkok')
                   AND r.created_at < ({$windowEnd} AT TIME ZONE 'Asia/Bangkok' + interval '1 day')
                 GROUP BY coalesce(r.agent_name, w.name, 'Unknown')
                 ORDER BY run_count DESC
                 LIMIT 10",
                Enum::FETCH_ASSOC
            )
        );

        $runsToday = (int) $runs['runs_today'];
        $errorRunsToday = (int) $runs['error_runs_today'];

        $runsPerDayFormatted = array_map(
            static fn (array $row): array => [
                'date' => $row['date'],
                'success' => (int) $row['success'],
                'error' => (int) $row['error'],
                'blocked' => (int) $row['blocked'],
                'other' => (int) $row['other'],
            ],
            $runsPerDay
        );
        $latencyPerDayFormatted = array_map(
            static fn (array $row): array => [
                'date' => $row['date'],
                'p50_ms' => $row['p50'] !== null ? (float) $row['p50'] : null,
                'p95_ms' => $row['p95'] !== null ? (float) $row['p95'] : null,
                'p99_ms' => $row['p99'] !== null ? (float) $row['p99'] : null,
            ],
            $latencyPerDay
        );
        $tokensCostPerDayFormatted = array_map(
            static fn (array $row): array => [
                'date' => $row['date'],
                'tokens_in' => (int) $row['tokens_in'],
                'tokens_out' => (int) $row['tokens_out'],
                'cost_usd' => (float) $row['cost_usd'],
            ],
            $tokensCostPerDay
        );
        $guardrailActionsPerDayFormatted = array_map(
            static fn (array $row): array => [
                'date' => $row['date'],
                'block' => (int) $row['block'],
                'mask' => (int) $row['mask'],
                'flag' => (int) $row['flag'],
            ],
            $guardrailActionsPerDay
        );

        $alerts = $this->computeAlerts(
            thresholds: $thresholds,
            monthlyBudgetUsd: $companyBudget['monthly_budget_usd'] !== null ? (float) $companyBudget['monthly_budget_usd'] : null,
            costThisMonthUsd: (float) $runs['cost_month'],
            runsToday: $runsToday,
            errorsToday: $errorRunsToday,
            guardrailTriggersToday: (int) $guardrailTriggers['n'],
            latencyP95Today: $runs['latency_p95_today'] !== null ? (float) $runs['latency_p95_today'] : null,
            costToday: (float) $runs['cost_today'],
            runsPerDay14d: $runsPerDayFormatted,
            latencyPerDay14d: $latencyPerDayFormatted,
            tokensCostPerDay14d: $tokensCostPerDayFormatted,
            guardrailActionsPerDay14d: $guardrailActionsPerDayFormatted,
        );

        return $this->jsonResponse([
            'alerts' => $alerts,
            'runs_today' => $runsToday,
            'tokens_this_month' => [
                'in' => (int) $runs['tokens_in_month'],
                'out' => (int) $runs['tokens_out_month'],
            ],
            'cost_this_month_usd' => (float) $runs['cost_month'],
            'error_rate' => $runsToday > 0 ? $errorRunsToday / $runsToday : 0.0,
            'guardrail_triggers' => (int) $guardrailTriggers['n'],
            'runs_per_day' => $runsPerDayFormatted,
            'latency_per_day' => $latencyPerDayFormatted,
            'tokens_cost_per_day' => $tokensCostPerDayFormatted,
            'guardrail_actions_per_day' => $guardrailActionsPerDayFormatted,
            'top_agents' => array_map(
                static fn (array $row): array => [
                    'name' => $row['name'],
                    'run_count' => (int) $row['run_count'],
                    'cost_usd' => (float) $row['cost_usd'],
                ],
                $topAgents
            ),
        ]);
    }

    /**
     * The Dashboard's alert banner (PRD-adjacent, added 2026-11-06) - 4 conditions,
     * each "today" vs. this company's own 14-day rolling baseline (the same
     * {$windowStart}/{$windowEnd} window the trend charts use, which excludes today -
     * see getDashboardSummary()'s own comment), not a fixed global number. That makes
     * the defaults reasonably self-calibrating across companies of very different
     * volume without per-company tuning being required - AlertThresholds still lets an
     * admin override the multipliers/floors via Settings when the defaults are wrong
     * for them specifically.
     *
     * Each condition pairs a *relative* check (today vs. multiple-of-baseline) with an
     * *absolute* floor - relative alone would fire on meaningless noise when the
     * baseline is near zero (e.g. latency going from 50ms to 110ms is still fast
     * despite being "2x"), and absolute alone wouldn't adapt to a company whose normal
     * volume is naturally high. Both must hold.
     *
     * @param array<string, int|float> $thresholds
     * @param list<array{date: string, success: int, error: int, blocked: int, other: int}> $runsPerDay14d
     * @param list<array{date: string, p50_ms: float|null, p95_ms: float|null, p99_ms: float|null}> $latencyPerDay14d
     * @param list<array{date: string, tokens_in: int, tokens_out: int, cost_usd: float}> $tokensCostPerDay14d
     * @param list<array{date: string, block: int, mask: int, flag: int}> $guardrailActionsPerDay14d
     * @return list<array{type: string, message: string}>
     */
    private function computeAlerts(
        array $thresholds,
        ?float $monthlyBudgetUsd,
        float $costThisMonthUsd,
        int $runsToday,
        int $errorsToday,
        int $guardrailTriggersToday,
        ?float $latencyP95Today,
        float $costToday,
        array $runsPerDay14d,
        array $latencyPerDay14d,
        array $tokensCostPerDay14d,
        array $guardrailActionsPerDay14d,
    ): array {
        $alerts = [];

        if ($monthlyBudgetUsd !== null && $monthlyBudgetUsd > 0) {
            $pctUsed = ($costThisMonthUsd / $monthlyBudgetUsd) * 100;
            if ($pctUsed >= $thresholds['quota_pct']) {
                $alerts[] = [
                    'type' => 'quota',
                    'message' => sprintf(
                        'This company has used %.0f%% of its monthly budget ($%.2f of $%.2f).',
                        $pctUsed,
                        $costThisMonthUsd,
                        $monthlyBudgetUsd
                    ),
                ];
            }
        }

        // Baseline is sum(errors)/sum(runs) across the whole 14-day window, NOT an
        // average of each day's own error rate - this company has many near-zero/zero-
        // run days, and averaging daily percentages would let one low-volume 100%-error
        // day skew the baseline far more than its real weight in total traffic warrants.
        $totalErrors14d = array_sum(array_column($runsPerDay14d, 'error'));
        $totalRuns14d = array_sum(array_map(
            static fn (array $row): int => $row['success'] + $row['error'] + $row['blocked'] + $row['other'],
            $runsPerDay14d
        ));
        $errorRateBaseline = $totalRuns14d > 0 ? $totalErrors14d / $totalRuns14d : 0.0;
        $errorRateToday = $runsToday > 0 ? $errorsToday / $runsToday : 0.0;

        if (
            $errorsToday >= $thresholds['error_rate_min_errors']
            && $errorRateToday >= ($thresholds['error_rate_min_pct'] / 100)
            && $errorRateToday >= $errorRateBaseline * $thresholds['error_rate_multiplier']
        ) {
            $alerts[] = [
                'type' => 'error_rate',
                'message' => sprintf(
                    "Today's error rate is %.0f%% (%d of %d runs), well above the usual %.0f%%.",
                    $errorRateToday * 100,
                    $errorsToday,
                    $runsToday,
                    $errorRateBaseline * 100
                ),
            ];
        }

        $guardrailBaselinePerDay = count($guardrailActionsPerDay14d) > 0
            ? array_sum(array_map(
                static fn (array $row): int => $row['block'] + $row['mask'] + $row['flag'],
                $guardrailActionsPerDay14d
            )) / count($guardrailActionsPerDay14d)
            : 0.0;

        // No `$guardrailBaselinePerDay > 0` guard here, unlike latency/cost below -
        // deliberately asymmetric, not an oversight. Latency/cost baselines of exactly 0
        // only happen when there were literally zero runs in the whole 14-day window (no
        // data to compare against at all, so "well above the usual ~0" would be a false
        // pretense). A guardrail baseline of 0 is different: it's a real, data-backed
        // measurement over 14 days of actual traffic (this company's own common case -
        // guardrails configured but rarely triggering), and a jump from a genuine "0/day
        // usual" to several today is exactly the kind of real behavior change this alert
        // exists to surface, gated by `guardrail_min_count` alone in that case.
        if (
            $guardrailTriggersToday >= $thresholds['guardrail_min_count']
            && $guardrailTriggersToday >= $guardrailBaselinePerDay * $thresholds['guardrail_multiplier']
        ) {
            $alerts[] = [
                'type' => 'guardrail',
                'message' => sprintf(
                    'Guardrail checks triggered %d times today, well above the usual ~%.0f/day.',
                    $guardrailTriggersToday,
                    $guardrailBaselinePerDay
                ),
            ];
        }

        $latencyValues14d = array_values(array_filter(
            array_column($latencyPerDay14d, 'p95_ms'),
            static fn (?float $v): bool => $v !== null
        ));
        $latencyBaseline = count($latencyValues14d) > 0 ? array_sum($latencyValues14d) / count($latencyValues14d) : 0.0;

        if (
            $latencyP95Today !== null
            && $latencyP95Today >= $thresholds['latency_min_ms']
            && $latencyBaseline > 0
            && $latencyP95Today >= $latencyBaseline * $thresholds['latency_multiplier']
        ) {
            $alerts[] = [
                'type' => 'latency',
                'message' => sprintf(
                    "Today's p95 latency is %.0fms, well above the usual ~%.0fms.",
                    $latencyP95Today,
                    $latencyBaseline
                ),
            ];
        }

        $costValues14d = array_column($tokensCostPerDay14d, 'cost_usd');
        $costBaselinePerDay = count($costValues14d) > 0 ? array_sum($costValues14d) / count($costValues14d) : 0.0;

        if (
            $costToday >= $thresholds['cost_min_usd']
            && $costBaselinePerDay > 0
            && $costToday >= $costBaselinePerDay * $thresholds['cost_multiplier']
        ) {
            $alerts[] = [
                'type' => 'cost',
                'message' => sprintf(
                    "Today's cost is $%.4f, well above the usual ~$%.4f/day.",
                    $costToday,
                    $costBaselinePerDay
                ),
            ];
        }

        return $alerts;
    }
}
