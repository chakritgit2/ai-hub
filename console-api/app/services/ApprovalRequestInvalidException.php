<?php

declare(strict_types=1);

namespace ConsoleApi\Services;

use RuntimeException;

/**
 * Thrown by RuntimeClient::decideApproval() when ai-runtime's
 * POST /internal/v1/approvals/{id} responds with a specific, documented 404 (no such
 * approval for this company) or 409 (already decided) - a real, distinguishable business
 * outcome, unlike every other RuntimeClient call, whose contract treats any non-2xx as an
 * opaque transport/service failure. ApprovalsController catches this ahead of the generic
 * RuntimeException to forward the real status/detail instead of flattening it into a 502 -
 * same role EvalRequestInvalidException plays for estimateEvalCost()/createEvalRun().
 */
class ApprovalRequestInvalidException extends RuntimeException
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
