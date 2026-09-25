<?php

declare(strict_types=1);

namespace ConsoleApi\Models;

use Phalcon\Mvc\Model;

/**
 * console.agents (PRD §4.4-A, §8.1). unique(company_id, name).
 * FORCE ROW LEVEL SECURITY applied in db/migrations/post/.
 */
class Agent extends Model
{
    public string $id;
    public string $company_id;
    public string $name;
    public ?string $description = null;
    public string $status = 'draft';
    public ?string $archived_at = null;
    public string $created_at;
    public string $updated_at;

    public function initialize(): void
    {
        $this->setSchema('console');
        $this->setSource('agents');

        $this->hasMany('id', AgentVersion::class, 'agent_id', ['alias' => 'versions']);
    }
}
