<?php

declare(strict_types=1);

use Phinx\Migration\AbstractMigration;

/**
 * `console.model_pricing` (PRD §8.2) — a genuinely platform-wide reference table, unlike
 * every other `console.*` table so far: no `company_id` column, because a model's price
 * is the same for every company. There is no admin UI for this yet (not in PRD §6.9's
 * page list) — rows are maintained directly via migration/SQL, same as this seed data.
 * Prices are USD per 1,000 tokens, approximate as of this writing — update via a later
 * migration or direct SQL as providers change pricing, there's deliberately no "last
 * verified" column to keep this simple until a real pricing-management need shows up.
 */
final class CreateModelPricingTable extends AbstractMigration
{
    public function up(): void
    {
        $this->execute(<<<SQL
            CREATE TABLE console.model_pricing (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                provider varchar(64) NOT NULL,
                model varchar(128) NOT NULL,
                input_per_1k numeric(10,6) NOT NULL,
                output_per_1k numeric(10,6) NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT model_pricing_provider_model_unique UNIQUE (provider, model),
                CONSTRAINT model_pricing_input_per_1k_check CHECK (input_per_1k >= 0),
                CONSTRAINT model_pricing_output_per_1k_check CHECK (output_per_1k >= 0)
            )
        SQL);

        $this->execute(<<<SQL
            INSERT INTO console.model_pricing (provider, model, input_per_1k, output_per_1k) VALUES
                ('openai', 'gpt-4o', 0.0025, 0.0100),
                ('openai', 'gpt-4o-mini', 0.00015, 0.00060),
                ('openai', 'gpt-4.1', 0.0020, 0.0080),
                ('openai', 'gpt-4.1-mini', 0.00040, 0.00160),
                ('openai', 'o4-mini', 0.0011, 0.0044),
                ('anthropic', 'claude-opus-5-5', 0.0150, 0.0750),
                ('anthropic', 'claude-sonnet-5', 0.0030, 0.0150),
                ('anthropic', 'claude-haiku-4-5', 0.0008, 0.0040),
                ('google', 'gemini-2.5-pro', 0.0025, 0.0100),
                ('google', 'gemini-2.5-flash', 0.00015, 0.00060)
        SQL);
    }

    public function down(): void
    {
        $this->execute('DROP TABLE IF EXISTS console.model_pricing');
    }
}
