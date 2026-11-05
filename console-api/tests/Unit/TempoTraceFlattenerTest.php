<?php

declare(strict_types=1);

namespace ConsoleApi\Tests\Unit;

use ConsoleApi\Services\TempoTraceFlattener;
use PHPUnit\Framework\TestCase;

final class TempoTraceFlattenerTest extends TestCase
{
    private TempoTraceFlattener $flattener;

    protected function setUp(): void
    {
        $this->flattener = new TempoTraceFlattener();
    }

    public function testFlattensResourceSpansShape(): void
    {
        $response = [
            'resourceSpans' => [
                [
                    'scopeSpans' => [
                        [
                            'spans' => [
                                [
                                    'spanId' => 'span-a',
                                    'parentSpanId' => '',
                                    'name' => 'dynamiq.agent_run',
                                    'startTimeUnixNano' => '1000000000',
                                    'endTimeUnixNano' => '3000000000',
                                    'attributes' => [],
                                    'status' => ['code' => 1],
                                ],
                            ],
                        ],
                    ],
                ],
            ],
        ];

        $result = $this->flattener->flatten('trace-1', $response);

        self::assertSame('trace-1', $result['trace_id']);
        self::assertCount(1, $result['spans']);
        $span = $result['spans'][0];
        self::assertSame('span-a', $span['span_id']);
        self::assertNull($span['parent_span_id']);
        self::assertSame('dynamiq.agent_run', $span['name']);
        self::assertSame(0.0, $span['start_ms_relative']);
        self::assertSame(2000.0, $span['duration_ms']);
        self::assertSame('ok', $span['status']);
    }

    public function testFlattensBatchesShape(): void
    {
        /** @var array<string, mixed> $response */
        $response = [
            'batches' => [
                [
                    'scopeSpans' => [
                        [
                            'spans' => [
                                [
                                    'spanId' => 'span-a',
                                    'name' => 'chat',
                                    'startTimeUnixNano' => '1000000000',
                                    'endTimeUnixNano' => '2000000000',
                                ],
                            ],
                        ],
                    ],
                ],
            ],
        ];

        $result = $this->flattener->flatten('trace-1', $response);

        self::assertCount(1, $result['spans']);
        self::assertSame('span-a', $result['spans'][0]['span_id']);
    }

    public function testComputesStartMsRelativeAgainstTheTrueTraceStartNotArrayOrder(): void
    {
        // Second span in array order starts EARLIER than the first - the relative time
        // must be computed against the true minimum, not array position.
        $response = [
            'resourceSpans' => [
                [
                    'scopeSpans' => [
                        [
                            'spans' => [
                                [
                                    'spanId' => 'later',
                                    'startTimeUnixNano' => '5000000000',
                                    'endTimeUnixNano' => '6000000000',
                                ],
                                [
                                    'spanId' => 'earlier',
                                    'startTimeUnixNano' => '1000000000',
                                    'endTimeUnixNano' => '2000000000',
                                ],
                            ],
                        ],
                    ],
                ],
            ],
        ];

        $result = $this->flattener->flatten('trace-1', $response);

        $byId = [];
        foreach ($result['spans'] as $span) {
            $byId[$span['span_id']] = $span;
        }

        self::assertSame(0.0, $byId['earlier']['start_ms_relative']);
        self::assertSame(4000.0, $byId['later']['start_ms_relative']);
    }

    public function testSortsSpansByStartTime(): void
    {
        $response = [
            'resourceSpans' => [
                [
                    'scopeSpans' => [
                        [
                            'spans' => [
                                ['spanId' => 'second', 'startTimeUnixNano' => '2000000000', 'endTimeUnixNano' => '3000000000'],
                                ['spanId' => 'first', 'startTimeUnixNano' => '1000000000', 'endTimeUnixNano' => '1500000000'],
                            ],
                        ],
                    ],
                ],
            ],
        ];

        $result = $this->flattener->flatten('trace-1', $response);

        self::assertSame(['first', 'second'], array_column($result['spans'], 'span_id'));
    }

    public function testUnwrapsEachOtlpAttributeTypedValue(): void
    {
        $response = [
            'resourceSpans' => [
                [
                    'scopeSpans' => [
                        [
                            'spans' => [
                                [
                                    'spanId' => 'span-a',
                                    'startTimeUnixNano' => '1000000000',
                                    'endTimeUnixNano' => '2000000000',
                                    'attributes' => [
                                        ['key' => 'gen_ai.operation.name', 'value' => ['stringValue' => 'chat']],
                                        ['key' => 'http.status_code', 'value' => ['intValue' => '200']],
                                        ['key' => 'cost_usd', 'value' => ['doubleValue' => 0.015]],
                                        ['key' => 'cached', 'value' => ['boolValue' => true]],
                                    ],
                                ],
                            ],
                        ],
                    ],
                ],
            ],
        ];

        $result = $this->flattener->flatten('trace-1', $response);

        self::assertSame(
            [
                'gen_ai.operation.name' => 'chat',
                'http.status_code' => 200,
                'cost_usd' => 0.015,
                'cached' => true,
            ],
            $result['spans'][0]['attributes']
        );
    }

    public function testMapsOtlpStatusCodes(): void
    {
        $makeSpan = static fn (string $id, ?int $code): array => [
            'spanId' => $id,
            'startTimeUnixNano' => '1000000000',
            'endTimeUnixNano' => '2000000000',
            'status' => $code === null ? null : ['code' => $code],
        ];

        $response = [
            'resourceSpans' => [
                [
                    'scopeSpans' => [
                        [
                            'spans' => [
                                $makeSpan('unset-explicit', 0),
                                $makeSpan('ok', 1),
                                $makeSpan('error', 2),
                                $makeSpan('unset-missing', null),
                            ],
                        ],
                    ],
                ],
            ],
        ];

        $result = $this->flattener->flatten('trace-1', $response);

        $byId = [];
        foreach ($result['spans'] as $span) {
            $byId[$span['span_id']] = $span['status'];
        }

        self::assertSame('unset', $byId['unset-explicit']);
        self::assertSame('ok', $byId['ok']);
        self::assertSame('error', $byId['error']);
        self::assertSame('unset', $byId['unset-missing']);
    }

    public function testReturnsEmptySpansForATraceWithNoSpans(): void
    {
        $result = $this->flattener->flatten('trace-1', ['resourceSpans' => []]);

        self::assertSame(['trace_id' => 'trace-1', 'spans' => []], $result);
    }
}
