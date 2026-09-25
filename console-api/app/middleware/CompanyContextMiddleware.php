<?php

declare(strict_types=1);

namespace ConsoleApi\Middleware;

use ConsoleApi\Services\CompanyContext;
use Phalcon\Di\Di;
use Phalcon\Events\Event;
use Phalcon\Mvc\Dispatcher;

/**
 * Binds the X-Company-Id header into the shared CompanyContext service before the
 * controller runs (PRD §7.2/§7.3). Every /admin/v1/* route except /me and /companies
 * is expected to carry this header; it is up to each controller to reject the request
 * (400/403) if it's required and missing, once membership checks are implemented.
 */
class CompanyContextMiddleware
{
    public function beforeDispatch(Event $event, Dispatcher $dispatcher): bool
    {
        $companyId = $_SERVER['HTTP_X_COMPANY_ID'] ?? null;

        /** @var CompanyContext $context */
        $context = Di::getDefault()->getShared('companyContext');
        $context->setCompanyId(($companyId !== null && $companyId !== '') ? $companyId : null);

        return true;
    }
}
