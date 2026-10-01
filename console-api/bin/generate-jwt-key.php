<?php

declare(strict_types=1);

/**
 * Generates the RS256 keypair console-api signs runtime/internal tokens with
 * (PRD §7.6) and writes the private key to CONSOLE_JWT_PRIVATE_KEY_PATH.
 *
 * Run once per environment (and again every rotation, per PRD §7.6's 90-day
 * policy — publish the new key in JWKS, start signing with it, remove the old
 * key from JWKS only after the longest token lifetime has passed):
 *
 *   php bin/generate-jwt-key.php
 *
 * On XAMPP's PHP for Windows, openssl_pkey_new() needs an explicit OPENSSL_CONF
 * pointing at an openssl.cnf, e.g.:
 *   OPENSSL_CONF="C:\xampp\php\extras\openssl\openssl.cnf" php bin/generate-jwt-key.php
 */

$consoleApiRoot = dirname(__DIR__);
require $consoleApiRoot . '/vendor/autoload.php';

if (file_exists($consoleApiRoot . '/.env')) {
    Dotenv\Dotenv::createImmutable($consoleApiRoot)->safeLoad();
}

$keyPath = $_ENV['CONSOLE_JWT_PRIVATE_KEY_PATH'] ?? './keys/console-signing-key.pem';
if (!str_starts_with($keyPath, '/') && !preg_match('/^[A-Za-z]:/', $keyPath)) {
    $keyPath = $consoleApiRoot . '/' . ltrim($keyPath, './');
}

if (file_exists($keyPath)) {
    fwrite(STDERR, "Refusing to overwrite existing key at {$keyPath} — delete it first if you mean to rotate.\n");
    exit(1);
}

$directory = dirname($keyPath);
if (!is_dir($directory) && !mkdir($directory, 0700, true) && !is_dir($directory)) {
    fwrite(STDERR, "Could not create directory {$directory}\n");
    exit(1);
}

$key = openssl_pkey_new(['private_key_bits' => 2048, 'private_key_type' => OPENSSL_KEYTYPE_RSA]);
if ($key === false) {
    fwrite(STDERR, "openssl_pkey_new() failed: " . openssl_error_string() . "\n");
    exit(1);
}

openssl_pkey_export($key, $pem);
file_put_contents($keyPath, $pem);
chmod($keyPath, 0600);

$kid = $_ENV['CONSOLE_JWT_KID'] ?? 'console-' . date('Y-m');
echo "Wrote private key to {$keyPath}\n";
echo "Set CONSOLE_JWT_KID={$kid} if not already (current .env value shown above where set).\n";
