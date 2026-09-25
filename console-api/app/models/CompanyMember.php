<?php

declare(strict_types=1);

namespace ConsoleApi\Models;

use Phalcon\Mvc\Model;

/**
 * console.company_members (PRD §7.2, §8.1) — a user's role within a company.
 * unique(company_id, user_id); role in (admin, developer, viewer).
 */
class CompanyMember extends Model
{
    public string $id;
    public string $company_id;
    public string $user_id;
    public string $role;
    public string $created_at;

    public function initialize(): void
    {
        $this->setSchema('console');
        $this->setSource('company_members');

        $this->belongsTo('company_id', Company::class, 'id', ['alias' => 'company']);
        $this->belongsTo('user_id', User::class, 'id', ['alias' => 'user']);
    }
}
