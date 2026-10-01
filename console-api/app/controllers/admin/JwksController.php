<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use ConsoleApi\Services\ConsoleJwks;
use Phalcon\Http\Response;

/**
 * GET /admin/.well-known/jwks.json — public, no auth (PRD §7.6). Exempted from
 * AuthMiddleware the same way GET /healthz is, since ai-runtime (and anything else
 * verifying console-api-issued tokens) must reach this without a token of its own.
 */
class JwksController extends ControllerBase
{
    public function getJwks(): Response
    {
        /** @var ConsoleJwks $jwks */
        $jwks = $this->getDI()->getShared('consoleJwks');

        return $this->jsonResponse($jwks->toArray());
    }
}
