<?php

declare(strict_types=1);

namespace ConsoleApi\Models;

use Phalcon\Mvc\Model;

/**
 * console.connections (PRD §7.4, §8.1) — company-scoped LLM/tool connections. Secrets
 * live in `connection_secrets` (no access for console_app), never on this table/model.
 * unique(company_id, name). FORCE ROW LEVEL SECURITY applied in db/migrations/post/.
 */
class Connection extends Model
{
    public string $id;
    public string $company_id;
    public string $name;
    public string $type;
    public ?string $api_base = null;
    public ?string $masked_hint = null;
    public int $max_concurrency = 4;
    public string $meta = '{}';
    public string $created_at;
    public string $updated_at;

    public function initialize(): void
    {
        $this->setSchema('console');
        $this->setSource('connections');
    }
}
