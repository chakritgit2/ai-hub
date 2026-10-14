<?php

declare(strict_types=1);

namespace ConsoleApi\Services;

use RuntimeException;

/**
 * Thrown by RuntimeClient::estimateEvalCost() when ai-runtime's
 * POST /internal/v1/evals/estimate responds with a specific, documented 404 or 422
 * (dataset_id/agent_version_id not found, or the agent version failed to compile -
 * ai-internal.yaml) - a real, distinguishable business outcome, unlike every other
 * RuntimeClient call, whose contract treats any non-2xx as an opaque transport/service
 * failure (see RuntimeClient::request()'s own comment). EvalsController::estimateEvalCost()
 * catches this ahead of the generic RuntimeException to forward the real status/detail
 * instead of flattening it into a 502.
 */
class EvalEstimateInvalidException extends RuntimeException
{
    public function __construct(private readonly int $statusCode, string $detail)
    {
        parent::__construct($detail);
    }

    public function getStatusCode(): int
    {
        return $this->statusCode;
    }
}
