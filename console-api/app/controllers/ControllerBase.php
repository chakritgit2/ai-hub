<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers;

use ConsoleApi\Services\CompanyContext;
use Phalcon\Http\Response;
use Phalcon\Mvc\Controller;

abstract class ControllerBase extends Controller
{
    protected function jsonResponse(mixed $data, int $statusCode = 200): Response
    {
        $response = new Response();
        $response->setStatusCode($statusCode);
        $response->setContentType('application/json');
        $response->setJsonContent($data);

        return $response;
    }

    /**
     * Standard body for every x-phase: 2 / x-phase: 3 (and otherwise unbuilt) operation:
     * HTTP 501 with {"error": "not_implemented"}.
     */
    protected function notImplemented(): Response
    {
        return $this->jsonResponse(['error' => 'not_implemented'], 501);
    }

    /**
     * The company_id bound from the X-Company-Id header by CompanyContextMiddleware
     * (PRD §7.2). Null on routes that don't require it (/me, /companies).
     */
    protected function getCompanyId(): ?string
    {
        /** @var CompanyContext $context */
        $context = $this->getDI()->getShared('companyContext');

        return $context->getCompanyId();
    }
}
