<?php

declare(strict_types=1);

use Phinx\Migration\AbstractMigration;

final class AddMaskedHintToTools extends AbstractMigration
{
    public function up(): void
    {
        $this->execute('ALTER TABLE console.tools ADD COLUMN masked_hint varchar(255)');
    }

    public function down(): void
    {
        $this->execute('ALTER TABLE console.tools DROP COLUMN masked_hint');
    }
}
