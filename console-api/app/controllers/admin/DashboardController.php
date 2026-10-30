<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Db\Enum;
use Phalcon\Http\Response;

/**
 * /dashboard — aggregate usage/cost summary for the current company (PRD §6.9
 * `DashboardSummary`), computed from `logs.runs`/`logs.guardrail_events`.
 *
 * `runs_today`/`error_rate`/`guardrail_triggers` use a "today" window;
 * `tokens_this_month`/`cost_this_month_usd` use a "this month" window (per field name) -
 * both bounded by Asia/Bangkok wall-clock time, matching the quota-reset convention
 * PRD §6.3 uses elsewhere ("reset at midnight Asia/Bangkok").
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

        $runs = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchOne(
                "SELECT
                    count(*) FILTER (WHERE created_at >= date_trunc('day', now() AT TIME ZONE 'Asia/Bangkok') AT TIME ZONE 'Asia/Bangkok') AS runs_today,
                    count(*) FILTER (WHERE status = 'error' AND created_at >= date_trunc('day', now() AT TIME ZONE 'Asia/Bangkok') AT TIME ZONE 'Asia/Bangkok') AS error_runs_today,
                    coalesce(sum(tokens_in) FILTER (WHERE created_at >= date_trunc('month', now() AT TIME ZONE 'Asia/Bangkok') AT TIME ZONE 'Asia/Bangkok'), 0) AS tokens_in_month,
                    coalesce(sum(tokens_out) FILTER (WHERE created_at >= date_trunc('month', now() AT TIME ZONE 'Asia/Bangkok') AT TIME ZONE 'Asia/Bangkok'), 0) AS tokens_out_month,
                    coalesce(sum(cost_usd) FILTER (WHERE created_at >= date_trunc('month', now() AT TIME ZONE 'Asia/Bangkok') AT TIME ZONE 'Asia/Bangkok'), 0) AS cost_month
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
        // queries above already use.
        $runsPerDay = $this->runInCompanyTransaction(
            static fn ($db) => $db->fetchAll(
                "SELECT to_char(d.day, 'YYYY-MM-DD') AS date, count(r.id) AS n
                 FROM generate_series(
                        date_trunc('day', now() AT TIME ZONE 'Asia/Bangkok') - interval '13 days',
                        date_trunc('day', now() AT TIME ZONE 'Asia/Bangkok'),
                        interval '1 day'
                      ) AS d(day)
                 LEFT JOIN logs.runs r
                   ON r.created_at >= (d.day AT TIME ZONE 'Asia/Bangkok')
                  AND r.created_at < ((d.day + interval '1 day') AT TIME ZONE 'Asia/Bangkok')
                 GROUP BY d.day
                 ORDER BY d.day",
                Enum::FETCH_ASSOC
            )
        );

        $runsToday = (int) $runs['runs_today'];
        $errorRunsToday = (int) $runs['error_runs_today'];

        return $this->jsonResponse([
            'runs_today' => $runsToday,
            'tokens_this_month' => [
                'in' => (int) $runs['tokens_in_month'],
                'out' => (int) $runs['tokens_out_month'],
            ],
            'cost_this_month_usd' => (float) $runs['cost_month'],
            'error_rate' => $runsToday > 0 ? $errorRunsToday / $runsToday : 0.0,
            'guardrail_triggers' => (int) $guardrailTriggers['n'],
            'runs_per_day' => array_map(
                static fn (array $row): array => ['date' => $row['date'], 'count' => (int) $row['n']],
                $runsPerDay
            ),
        ]);
    }
}
