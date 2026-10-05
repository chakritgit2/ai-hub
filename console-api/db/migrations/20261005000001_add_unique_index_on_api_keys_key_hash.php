<?php

declare(strict_types=1);

use Phinx\Migration\AbstractMigration;

final class AddUniqueIndexOnApiKeysKeyHash extends AbstractMigration
{
    public function up(): void
    {
        // console.resolve_api_key (db/migrations/post/003_resolve_api_key_function.sql)
        // looks up by key_hash on every single gateway request with no index backing it
        // at all today - a sequential scan on console.api_keys for the hottest path in
        // the whole service. Unique, not just indexed: nothing in the schema otherwise
        // guarantees two different rows can't end up with the same hash (an application
        // bug in key generation, not a hash collision - the hashed input has 192 bits of
        // entropy, see ApiKeysController::createApiKey) beyond the accidental, indirect
        // uniqueness of deployments.slug that resolve_api_key's join happens to rely on.
        $this->execute('CREATE UNIQUE INDEX api_keys_key_hash_unique_idx ON console.api_keys (key_hash)');
    }

    public function down(): void
    {
        $this->execute('DROP INDEX IF EXISTS console.api_keys_key_hash_unique_idx');
    }
}
