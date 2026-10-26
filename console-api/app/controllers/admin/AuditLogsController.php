<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Db\Enum;
use Phalcon\Http\Response;

/**
 * /audit-logs — platform_admin only, no X-Company-Id (same shape as Tenants/Companies).
 * Reads via the `dbPlatform` connection (console_platform role) instead of the usual
 * `db` (console_app), since only console_platform's `platform_admin_read_all` RLS policy
 * (db/migrations/post/002_rls_policies.sql) can see every company's rows in one query,
 * including the company_id IS NULL platform-level events (e.g. an SSO login claiming an
 * unregistered company ref) that console_app can never read regardless of which
 * app.company_id is set.
 */
class AuditLogsController extends ControllerBase
{
    private const DEFAULT_LIMIT = 50;
    private const MAX_LIMIT = 200;
    private const MAX_OFFSET = 100_000;

    private const LIST_SQL = "SELECT l.id, l.company_id, c.code AS company_code, c.name AS company_name,
                l.user_id, u.email AS user_email, l.action, l.entity, l.entity_id, l.diff, l.created_at
         FROM console.audit_logs l
         LEFT JOIN console.companies c ON c.id = l.company_id
         LEFT JOIN console.users u ON u.id = l.user_id
         WHERE (:from::timestamptz IS NULL OR l.created_at >= :from::timestamptz)
           AND (:to::timestamptz IS NULL OR l.created_at <= :to::timestamptz)
         ORDER BY l.created_at DESC
         LIMIT :fetch_limit OFFSET :offset";

    public function listAuditLogs(): Response
    {
        if (($error = $this->requirePlatformAdmin()) !== null) {
            return $error;
        }

        $from = $this->request->getQuery('from');
        $to = $this->request->getQuery('to');
        foreach (['from' => $from, 'to' => $to] as $name => $value) {
            if ($value !== null && $value !== '' && strtotime((string) $value) === false) {
                return $this->jsonResponse(['error' => "invalid_{$name}"], 400);
            }
        }

        $limit = self::DEFAULT_LIMIT;
        if (($rawLimit = $this->request->getQuery('limit')) !== null && $rawLimit !== '') {
            if (!ctype_digit((string) $rawLimit) || (int) $rawLimit < 1) {
                return $this->jsonResponse(['error' => 'invalid_limit'], 400);
            }
            $limit = min((int) $rawLimit, self::MAX_LIMIT);
        }

        $offset = 0;
        if (($rawOffset = $this->request->getQuery('offset')) !== null && $rawOffset !== '') {
            if (!ctype_digit((string) $rawOffset) || (int) $rawOffset > self::MAX_OFFSET) {
                return $this->jsonResponse(['error' => 'invalid_offset'], 400);
            }
            $offset = (int) $rawOffset;
        }

        $db = $this->getDI()->getShared('dbPlatform');
        // Fetch one extra row instead of a separate COUNT(*) — audit_logs is append-only
        // and can grow large, and no caller here needs a total, just "is there a next page".
        $rows = $db->fetchAll(self::LIST_SQL, Enum::FETCH_ASSOC, [
            'from' => $from !== '' ? $from : null,
            'to' => $to !== '' ? $this->endOfDayIfDateOnly((string) $to) : null,
            'fetch_limit' => $limit + 1,
            'offset' => $offset,
        ]);

        $hasMore = count($rows) > $limit;
        $rows = array_slice($rows, 0, $limit);

        return $this->jsonResponse([
            'items' => array_map([$this, 'formatEntry'], $rows),
            'has_more' => $hasMore,
        ]);
    }

    /**
     * `to` comes from the Settings UI's <input type="date">, a bare "YYYY-MM-DD" with no
     * time component — cast straight to timestamptz that means midnight, which would make
     * an inclusive `<=` filter exclude the entire day the admin actually picked. Widening a
     * plain date to its last instant keeps the filter inclusive of that whole day; a value
     * that already carries a time (or any other format strtotime() accepts) passes through
     * unchanged.
     */
    private function endOfDayIfDateOnly(string $value): string
    {
        return preg_match('/^\d{4}-\d{2}-\d{2}$/', $value) === 1 ? $value . ' 23:59:59.999999' : $value;
    }

    /**
     * @param array<string, mixed> $row
     * @return array<string, mixed>
     */
    private function formatEntry(array $row): array
    {
        return [
            'id' => $row['id'],
            'company_id' => $row['company_id'],
            'company_code' => $row['company_code'],
            'company_name' => $row['company_name'],
            'user_id' => $row['user_id'],
            'user_email' => $row['user_email'],
            'action' => $row['action'],
            'entity' => $row['entity'],
            'entity_id' => $row['entity_id'],
            'diff' => $row['diff'] !== null ? json_decode((string) $row['diff'], true) : null,
            'created_at' => $row['created_at'],
        ];
    }
}
