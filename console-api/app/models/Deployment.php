<?php

declare(strict_types=1);

namespace ConsoleApi\Models;

use Phalcon\Mvc\Model;

/**
 * console.deployments (PRD §8.1) — gateway-facing config for a published agent_version.
 * unique(slug). FORCE ROW LEVEL SECURITY applied in db/migrations/post/.
 */
class Deployment extends Model
{
    public string $id;
    public string $company_id;
    public string $slug;
    public string $environment = 'production';
    public string $agent_version_id;
    public int $rate_limit_per_min = 60;
    public ?int $daily_token_limit = null;
    public ?string $daily_cost_limit_usd = null;
    public string $allowed_origins = '[]';
    public string $guardrail_overrides = '{}';
    public string $output_mode = 'stream';
    public bool $allow_write_tools = false;
    public int $conversation_ttl_days = 30;
    public int $config_version = 1;
    public bool $enabled = true;
    public string $created_at;
    public string $updated_at;

    public function initialize(): void
    {
        $this->setSchema('console');
        $this->setSource('deployments');

        $this->belongsTo('agent_version_id', AgentVersion::class, 'id', ['alias' => 'agentVersion']);
    }
}
