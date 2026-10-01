<?php

declare(strict_types=1);

namespace ConsoleApi\Services;

use Firebase\JWT\JWK;
use Firebase\JWT\JWT;
use Firebase\JWT\Key;
use GuzzleHttp\Client;
use Phalcon\Db\Adapter\Pdo\AbstractPdo;
use Phalcon\Db\Enum;
use RuntimeException;
use Throwable;

/**
 * Verifies the SSO JWT issued by the existing Phalcon system (RS256 + JWKS, PRD §7.1/§7.6)
 * and syncs `console.users` / `console.company_members` from its claims on each login.
 *
 * The existing system's real JWKS URL / claim shape is still an open question (PRD §15.2),
 * so this reads `SSO_JWKS_URL` / `SSO_ISSUER` / `SSO_AUDIENCE` from config and assumes the
 * claim shape PRD §7.1 already commits to: `sub`, `email`, `companies: [{ref, role}]`.
 */
class AuthService
{
    private const JWKS_CACHE_TTL_SECONDS = 300;

    /** @var array<string, array{fetchedAt: int, keys: array<string, Key>}> */
    private static array $jwksCache = [];

    public function __construct(
        private readonly string $jwksUrl,
        private readonly string $issuer,
        private readonly string $audience,
        private readonly AbstractPdo $db,
        private readonly Client $http = new Client(['timeout' => 5])
    ) {
    }

    /**
     * @return array{sub: string, email: string, companies: array<int, array{ref: string, role: string}>}
     */
    public function verifySsoJwt(string $jwt): array
    {
        if ($this->jwksUrl === '') {
            throw new RuntimeException('AuthService: SSO_JWKS_URL is not configured (PRD §7.1/§7.6).');
        }

        $keys = $this->fetchJwks();

        try {
            $payload = JWT::decode($jwt, $keys);
        } catch (Throwable $exception) {
            throw new InvalidSsoTokenException('invalid_sso_token: ' . $exception->getMessage(), 0, $exception);
        }

        if ($this->issuer !== '' && ($payload->iss ?? null) !== $this->issuer) {
            throw new InvalidSsoTokenException('invalid_sso_token: unexpected issuer');
        }

        if ($this->audience !== '') {
            $audiences = is_array($payload->aud ?? null) ? $payload->aud : [$payload->aud ?? null];
            if (!in_array($this->audience, $audiences, true)) {
                throw new InvalidSsoTokenException('invalid_sso_token: unexpected audience');
            }
        }

        if (empty($payload->sub) || !is_string($payload->sub)) {
            throw new InvalidSsoTokenException('invalid_sso_token: missing sub claim');
        }

        if (empty($payload->email) || !is_string($payload->email)) {
            throw new InvalidSsoTokenException('invalid_sso_token: missing email claim');
        }

        $companies = [];
        foreach ((array) ($payload->companies ?? []) as $entry) {
            $entry = (array) $entry;
            if (!isset($entry['ref'], $entry['role']) || !is_string($entry['ref']) || !is_string($entry['role'])) {
                throw new InvalidSsoTokenException('invalid_sso_token: malformed companies claim');
            }
            $companies[] = ['ref' => $entry['ref'], 'role' => $entry['role']];
        }

        return [
            'sub' => $payload->sub,
            'email' => $payload->email,
            'companies' => $companies,
        ];
    }

    /**
     * Upserts `console.users` and `console.company_members` from decoded SSO claims
     * (PRD §7.1, called by AuthMiddleware on every request). Company refs with no
     * matching `console.companies.external_ref` are skipped rather than auto-created —
     * companies are provisioned by platform_admin only (PRD §7.2) — matching PRD §12's
     * "JWT company not yet registered: company skipped + audit" edge case (audit logging
     * itself is AuditMiddleware's job, still a stub — see app/middleware/AuditMiddleware.php).
     *
     * Each company's membership row is written in its own transaction with
     * `app.company_id` set to that company (via CompanyContext), because
     * `console.company_members` is RLS-protected and there is no cross-company write
     * bypass for the app's DB role (PRD §7.3) — only `console_app`/`console_platform`,
     * and `console_platform`'s extra policy is SELECT-only.
     *
     * @param array{sub: string, email: string, companies: array<int, array{ref: string, role: string}>} $claims
     * @return array{
     *     id: string,
     *     email: string,
     *     is_platform_admin: bool,
     *     companies: array<int, array{company_id: string, role: string}>,
     *     skipped_company_refs: array<int, string>
     * }
     */
    public function syncUser(array $claims): array
    {
        $user = $this->db->fetchOne(
            'SELECT id, is_platform_admin FROM console.users WHERE external_sub = :sub',
            Enum::FETCH_ASSOC,
            ['sub' => $claims['sub']]
        );

        if ($user === false) {
            $user = $this->db->fetchOne(
                'INSERT INTO console.users (external_sub, email) VALUES (:sub, :email)
                 RETURNING id, is_platform_admin',
                Enum::FETCH_ASSOC,
                ['sub' => $claims['sub'], 'email' => $claims['email']]
            );
        } else {
            $this->db->execute(
                'UPDATE console.users SET email = :email, updated_at = now() WHERE id = :id',
                ['email' => $claims['email'], 'id' => $user['id']]
            );
        }

        $userId = (string) $user['id'];
        $companies = [];
        $skippedRefs = [];

        foreach ($claims['companies'] as $entry) {
            $company = $this->db->fetchOne(
                'SELECT id FROM console.companies WHERE external_ref = :ref',
                Enum::FETCH_ASSOC,
                ['ref' => $entry['ref']]
            );

            if ($company === false) {
                $skippedRefs[] = $entry['ref'];
                continue;
            }

            $companyId = (string) $company['id'];
            $this->upsertCompanyMember($companyId, $userId, $entry['role']);
            $companies[] = ['company_id' => $companyId, 'role' => $entry['role']];
        }

        return [
            'id' => $userId,
            'email' => $claims['email'],
            'is_platform_admin' => (bool) $user['is_platform_admin'],
            'companies' => $companies,
            'skipped_company_refs' => $skippedRefs,
        ];
    }

    private function upsertCompanyMember(string $companyId, string $userId, string $role): void
    {
        $this->db->begin();

        try {
            $companyContext = new CompanyContext();
            $companyContext->setCompanyId($companyId);
            $companyContext->applyToConnection($this->db);

            $this->db->execute(
                'INSERT INTO console.company_members (company_id, user_id, role)
                 VALUES (:company_id, :user_id, :role)
                 ON CONFLICT (company_id, user_id) DO UPDATE SET role = EXCLUDED.role',
                ['company_id' => $companyId, 'user_id' => $userId, 'role' => $role]
            );

            $this->db->commit();
        } catch (Throwable $exception) {
            $this->db->rollback();

            throw $exception;
        }
    }

    /**
     * @return array<string, Key>
     */
    private function fetchJwks(): array
    {
        $cached = self::$jwksCache[$this->jwksUrl] ?? null;
        if ($cached !== null && (time() - $cached['fetchedAt']) < self::JWKS_CACHE_TTL_SECONDS) {
            return $cached['keys'];
        }

        try {
            $body = (string) $this->http->get($this->jwksUrl)->getBody();
            $jwks = json_decode($body, true, 512, JSON_THROW_ON_ERROR);
        } catch (Throwable $exception) {
            throw new RuntimeException(
                'AuthService: failed to fetch JWKS from ' . $this->jwksUrl . ': ' . $exception->getMessage(),
                0,
                $exception
            );
        }

        $keys = JWK::parseKeySet($jwks);
        self::$jwksCache[$this->jwksUrl] = ['fetchedAt' => time(), 'keys' => $keys];

        return $keys;
    }
}
