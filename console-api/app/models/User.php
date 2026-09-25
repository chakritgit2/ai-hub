<?php

declare(strict_types=1);

namespace ConsoleApi\Models;

use Phalcon\Mvc\Model;

/**
 * console.users (PRD §8.1). Synced from SSO JWT claims on each login (PRD §7.1);
 * not company-scoped, so it carries no company_id / RLS.
 */
class User extends Model
{
    public string $id;
    public string $external_sub;
    public string $email;
    public bool $is_platform_admin = false;
    public string $created_at;
    public string $updated_at;

    public function initialize(): void
    {
        $this->setSchema('console');
        $this->setSource('users');

        $this->hasMany('id', CompanyMember::class, 'user_id', ['alias' => 'memberships']);
    }
}
