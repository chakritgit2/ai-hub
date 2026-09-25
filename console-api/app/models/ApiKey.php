<?php

declare(strict_types=1);

namespace ConsoleApi\Models;

use Phalcon\Mvc\Model;

/**
 * console.api_keys (PRD §8.1) — resolved by the gateway via the SECURITY DEFINER
 * function `console.resolve_api_key(key_hash, slug)` (PRD §7.3).
 * FORCE ROW LEVEL SECURITY applied in db/migrations/post/.
 */
class ApiKey extends Model
{
    public string $id;
    public string $company_id;
    public string $name;
    public string $key_hash;
    public string $prefix;
    public string $allowed_ips = '[]';
    public ?int $rate_limit_per_min = null;
    public ?string $daily_cost_limit_usd = null;
    public ?string $last_used_at = null;
    public ?string $created_by = null;
    public ?string $revoked_at = null;
    public string $created_at;

    public function initialize(): void
    {
        $this->setSchema('console');
        $this->setSource('api_keys');
    }
}
