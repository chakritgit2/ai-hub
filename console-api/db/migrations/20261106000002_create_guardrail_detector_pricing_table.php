<?php

declare(strict_types=1);

use Phinx\Migration\AbstractMigration;

/**
 * Per-company $/request pricing for the 3 Azure-backed guardrail detectors
 * (prompt_injection/pii_model/content_moderation - PRD §6.4, see `ai/app/services/
 * guardrails.py`'s module docstring for why these are Azure AI Content Safety/Language,
 * not a HuggingFace/Lakera/Replicate model). Azure bills these per request, not per
 * token, so there's no automatic pricing source the way litellm's `cost_per_token()`
 * gives real LLM run cost - unlike `console.model_pricing` (a flat, platform-wide
 * reference table used only for eval-cost *estimates*), this is company-scoped: each
 * company's own Azure subscription/contract can have a different real rate, and this
 * feeds `logs.runs.guardrail_cost_usd` for an ACTUAL run, not an estimate.
 *
 * One row per (company_id, check_type), created lazily on first PUT - a company that
 * never visits Settings' Guardrail pricing tab simply has no rows, and
 * `guardrail_cost_usd` stays 0 for that detector (never guessed) until priced.
 */
final class CreateGuardrailDetectorPricingTable extends AbstractMigration
{
    public function up(): void
    {
        $this->execute(<<<SQL
            CREATE TABLE console.guardrail_detector_pricing (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                company_id uuid NOT NULL,
                check_type varchar(32) NOT NULL,
                price_per_request_usd numeric NOT NULL DEFAULT 0,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                UNIQUE (company_id, check_type)
            )
        SQL);
    }

    public function down(): void
    {
        $this->execute('DROP TABLE IF EXISTS console.guardrail_detector_pricing');
    }
}
