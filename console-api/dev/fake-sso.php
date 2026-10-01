<?php

declare(strict_types=1);

/**
 * Dev-only stand-in for the existing Phalcon system's SSO (PRD §7.1) — the real thing
 * isn't reachable from local dev yet (PRD §15 open question 2). NOT part of the
 * architecture; nothing else in the repo references this file.
 *
 * Run: php -S 127.0.0.1:8999 console-api/dev/fake-sso.php
 * Then open http://127.0.0.1:8999/ and log in — it redirects to web's /dev-login with a
 * real RS256-signed token, and serves the matching JWKS at /jwks.json (point
 * console-api's SSO_JWKS_URL at this).
 *
 * On XAMPP's PHP for Windows, openssl_pkey_new() silently fails without an explicit
 * OPENSSL_CONF pointing at an openssl.cnf (php.ini's openssl.cafile alone isn't enough) —
 * if you see "Cannot get key from parameter 1", run with e.g.
 * OPENSSL_CONF="C:\xampp\php\extras\openssl\openssl.cnf" php -S ...
 */

require __DIR__ . '/../vendor/autoload.php';

use Firebase\JWT\JWT;

$keyPath = __DIR__ . '/fake-sso-key.pem';
if (!file_exists($keyPath)) {
    $key = openssl_pkey_new(['private_key_bits' => 2048, 'private_key_type' => OPENSSL_KEYTYPE_RSA]);
    openssl_pkey_export($key, $pem);
    file_put_contents($keyPath, $pem);
}
$privateKeyPem = file_get_contents($keyPath);
$details = openssl_pkey_get_details(openssl_pkey_get_private($privateKeyPem));

function b64url(string $bin): string
{
    return rtrim(strtr(base64_encode($bin), '+/', '-_'), '=');
}

$path = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH);

if ($path === '/jwks.json') {
    header('Content-Type: application/json');
    echo json_encode(['keys' => [[
        'kty' => 'RSA',
        'kid' => 'fake-sso-dev',
        'alg' => 'RS256',
        'use' => 'sig',
        'n' => b64url($details['rsa']['n']),
        'e' => b64url($details['rsa']['e']),
    ]]]);
    exit;
}

if ($path === '/login' && ($_SERVER['REQUEST_METHOD'] ?? '') === 'POST') {
    $email = trim((string) ($_POST['email'] ?? 'developer@advws.com'));
    $sub = 'dev-' . preg_replace('/[^a-z0-9]+/i', '-', strtolower($email));

    $companies = [];
    foreach (explode(',', (string) ($_POST['companies'] ?? '')) as $entry) {
        $entry = trim($entry);
        if ($entry === '') {
            continue;
        }
        [$ref, $role] = array_pad(explode(':', $entry, 2), 2, 'admin');
        $companies[] = ['ref' => trim($ref), 'role' => trim($role)];
    }

    $jwt = JWT::encode([
        'iss' => 'https://sso.example.internal',
        'aud' => 'dynamiq-console',
        'sub' => $sub,
        'email' => $email,
        'companies' => $companies,
        'exp' => time() + 3600,
    ], $privateKeyPem, 'RS256', 'fake-sso-dev');

    $webBase = getenv('DEV_LOGIN_WEB_BASE_URL') ?: 'http://localhost:5173';
    header('Location: ' . $webBase . '/dev-login#token=' . $jwt);
    http_response_code(302);
    exit;
}

header('Content-Type: text/html; charset=utf-8');
?>
<!doctype html>
<title>Dynamiq Console — dev SSO</title>
<body style="font-family: system-ui, sans-serif; max-width: 32rem; margin: 3rem auto; padding: 0 1rem;">
<h1>Dev SSO login</h1>
<p>Stands in for the existing Phalcon system's SSO until it's wired up (PRD §7.1). Local dev only.</p>
<form method="post" action="/login">
	<p>
		<label style="display:block">Email
			<input name="email" value="developer@advws.com" style="width:100%; box-sizing:border-box">
		</label>
	</p>
	<p>
		<label style="display:block">Companies (<code>external_ref:role</code>, comma-separated)
			<input name="companies" value="ext-test-co:admin" style="width:100%; box-sizing:border-box">
		</label>
		<small>role is one of admin / developer / viewer. A ref with no matching
			<code>console.companies.external_ref</code> is skipped on sync (PRD §12).</small>
	</p>
	<p><button type="submit">Log in</button></p>
</form>
</body>
