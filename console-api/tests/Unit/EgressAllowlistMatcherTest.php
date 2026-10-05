<?php

declare(strict_types=1);

namespace ConsoleApi\Tests\Unit;

use ConsoleApi\Services\EgressAllowlistMatcher;
use PHPUnit\Framework\TestCase;

final class EgressAllowlistMatcherTest extends TestCase
{
    public function testExactHostMatchIsAllowed(): void
    {
        $rows = [['host_pattern' => 'api.openai.com', 'port' => null, 'allow_private_ip' => false]];

        self::assertTrue(EgressAllowlistMatcher::hostAllowed($rows, 'api.openai.com'));
    }

    public function testMatchIsCaseInsensitive(): void
    {
        $rows = [['host_pattern' => 'API.OpenAI.com', 'port' => null, 'allow_private_ip' => false]];

        self::assertTrue(EgressAllowlistMatcher::hostAllowed($rows, 'api.openai.com'));
    }

    public function testWildcardMatchesSubdomain(): void
    {
        $rows = [['host_pattern' => '*.example.com', 'port' => null, 'allow_private_ip' => false]];

        self::assertTrue(EgressAllowlistMatcher::hostAllowed($rows, 'api.example.com'));
    }

    public function testWildcardDoesNotMatchBareDomain(): void
    {
        $rows = [['host_pattern' => '*.example.com', 'port' => null, 'allow_private_ip' => false]];

        self::assertFalse(EgressAllowlistMatcher::hostAllowed($rows, 'example.com'));
    }

    public function testWildcardDoesNotMatchUnrelatedSuffix(): void
    {
        $rows = [['host_pattern' => '*.example.com', 'port' => null, 'allow_private_ip' => false]];

        self::assertFalse(EgressAllowlistMatcher::hostAllowed($rows, 'evilexample.com'));
    }

    public function testNoMatchingRowIsNotAllowed(): void
    {
        $rows = [['host_pattern' => 'api.openai.com', 'port' => null, 'allow_private_ip' => false]];

        self::assertFalse(EgressAllowlistMatcher::hostAllowed($rows, 'internal.corp'));
    }

    public function testEmptyRowsIsNotAllowed(): void
    {
        self::assertFalse(EgressAllowlistMatcher::hostAllowed([], 'api.openai.com'));
    }

    public function testPortScopedRowRejectsADifferentPort(): void
    {
        $rows = [['host_pattern' => 'internal.corp', 'port' => 8443, 'allow_private_ip' => false]];

        self::assertFalse(EgressAllowlistMatcher::hostAllowed($rows, 'internal.corp', 443));
    }

    public function testPortScopedRowAllowsTheMatchingPort(): void
    {
        $rows = [['host_pattern' => 'internal.corp', 'port' => 8443, 'allow_private_ip' => false]];

        self::assertTrue(EgressAllowlistMatcher::hostAllowed($rows, 'internal.corp', 8443));
    }

    public function testRowWithNullPortAllowsAnyPort(): void
    {
        $rows = [['host_pattern' => 'api.openai.com', 'port' => null, 'allow_private_ip' => false]];

        self::assertTrue(EgressAllowlistMatcher::hostAllowed($rows, 'api.openai.com', 9999));
    }

    public function testUnknownRequestPortDegradesToHostOnlyMatch(): void
    {
        // Mirrors ai's SafeHttpClient._matching_entries: a caller with no port to compare
        // (the null default) must not reject every port-scoped row outright.
        $rows = [['host_pattern' => 'internal.corp', 'port' => 8443, 'allow_private_ip' => false]];

        self::assertTrue(EgressAllowlistMatcher::hostAllowed($rows, 'internal.corp'));
    }

    public function testDefaultPortForSchemeMatchesSafeHttpClients(): void
    {
        self::assertSame(443, EgressAllowlistMatcher::defaultPortForScheme('https'));
        self::assertSame(80, EgressAllowlistMatcher::defaultPortForScheme('http'));
        self::assertSame(443, EgressAllowlistMatcher::defaultPortForScheme(null));
    }
}
