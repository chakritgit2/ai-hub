<?php

declare(strict_types=1);

/**
 * Dev-only helper — mints a 60-second internal call token (PRD §9.2/§7.6, aud=ai-internal)
 * via the real DI container/TokenIssuer, for smoke-testing ai-runtime's /internal/v1/*
 * endpoints without hand-rolling a one-off PHP snippet each time. NOT part of the
 * architecture; nothing else in the repo references this file.
 *
 * Run: php console-api/dev/mint-internal-token.php
 * (needs console-api/.env configured and keys/console-signing-key.pem present — see
 * bin/generate-jwt-key.php)
 */

$consoleApiRoot = __DIR__ . '/..';
require $consoleApiRoot . '/vendor/autoload.php';

$di = require $consoleApiRoot . '/app/config/services.php';

/** @var \ConsoleApi\Services\TokenIssuer $tokenIssuer */
$tokenIssuer = $di->getShared('tokenIssuer');

echo $tokenIssuer->issueInternalToken() . "\n";
