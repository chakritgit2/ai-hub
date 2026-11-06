<?php

declare(strict_types=1);

namespace ConsoleApi\Services;

use Phalcon\Db\Adapter\Pdo\AbstractPdo;
use Phalcon\Db\Enum;

/**
 * Per-company $/request pricing for the 3 Azure-backed guardrail detectors
 * (`console.guardrail_detector_pricing`, added 2026-11-06) - feeds `ai/`'s
 * `logs.runs.guardrail_cost_usd` for an actual run (see the Phinx migration's own
 * docstring for why this exists and isn't automatic the way LLM token cost is).
 */
final class GuardrailDetectorPricing
{
    /** The only 3 check_types that cost anything - see
     * ai/app/services/agent_spec.py's MODEL_BASED_CHECK_TYPES, the Python-side source of
     * truth this list must stay in sync with. */
    public const CHECK_TYPES = ['prompt_injection', 'pii_model', 'content_moderation'];

    /**
     * Always returns exactly 3 rows (one per CHECK_TYPES, in that order), defaulting an
     * uncustomized check_type's price to 0.0 - "never configured" means "never charged
     * for it", not a guessed number.
     *
     * @return list<array{check_type: string, price_per_request_usd: float}>
     */
    public static function resolve(AbstractPdo $db): array
    {
        $rows = $db->fetchAll(
            'SELECT check_type, price_per_request_usd FROM console.guardrail_detector_pricing',
            Enum::FETCH_ASSOC
        );
        $byType = [];
        foreach ($rows as $row) {
            $byType[$row['check_type']] = (float) $row['price_per_request_usd'];
        }

        return array_map(
            static fn (string $checkType): array => [
                'check_type' => $checkType,
                'price_per_request_usd' => $byType[$checkType] ?? 0.0,
            ],
            self::CHECK_TYPES
        );
    }
}
