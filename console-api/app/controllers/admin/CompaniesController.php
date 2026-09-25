<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Http\Response;

/**
 * /companies — platform_admin only (PRD §8.1 `companies` table). Unlike every other
 * controller here, these routes do not require X-Company-Id.
 */
class CompaniesController extends ControllerBase
{
    public function listCompanies(): Response
    {
        return $this->notImplemented();
    }

    public function createCompany(): Response
    {
        return $this->notImplemented();
    }

    public function suspendCompany(string $id): Response
    {
        return $this->notImplemented();
    }

    public function activateCompany(string $id): Response
    {
        return $this->notImplemented();
    }

    /**
     * Crypto-shredding — destroys the company's wrapped DEK so its secrets become
     * undecryptable immediately (PRD §12, `company_keys` table).
     */
    public function destroyCompanyDek(string $id): Response
    {
        return $this->notImplemented();
    }

    public function listCompanyMembers(string $id): Response
    {
        return $this->notImplemented();
    }
}
