<?php

declare(strict_types=1);

namespace ConsoleApi\Models;

use Phalcon\Mvc\Model;

/**
 * console.companies (PRD §8.1). Not subject to RLS itself (RLS applies to tables that
 * carry company_id); `company_members` is the join table into this one.
 */
class Company extends Model
{
    public string $id;
    public string $code;
    public string $name;
    public ?string $external_ref = null;
    public string $status = 'active';
    public ?string $monthly_budget_usd = null;
    public string $created_at;
    public string $updated_at;

    public function initialize(): void
    {
        $this->setSchema('console');
        $this->setSource('companies');

        $this->hasMany('id', CompanyMember::class, 'company_id', ['alias' => 'members']);
    }
}
