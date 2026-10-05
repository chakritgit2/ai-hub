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
}
