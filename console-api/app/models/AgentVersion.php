<?php

declare(strict_types=1);

namespace ConsoleApi\Models;

use Phalcon\Mvc\Model;

/**
 * console.agent_versions (PRD §4.4-A, §8.1). unique(agent_id, version_no).
 * FORCE ROW LEVEL SECURITY applied in db/migrations/post/.
 */
class AgentVersion extends Model
{
    public string $id;
    public string $company_id;
    public string $agent_id;
    public int $version_no;
    public string $spec;
    public string $spec_version;
    public ?string $compiled_definition = null;
    public ?string $compiler_version = null;
    public ?string $dynamiq_version = null;
    public bool $is_published = false;
    public ?string $published_by = null;
    public ?string $published_at = null;
    public string $created_at;
    public string $updated_at;

    public function initialize(): void
    {
        $this->setSchema('console');
        $this->setSource('agent_versions');

        $this->belongsTo('agent_id', Agent::class, 'id', ['alias' => 'agent']);
        $this->hasMany('id', Deployment::class, 'agent_version_id', ['alias' => 'deployments']);
    }
}
