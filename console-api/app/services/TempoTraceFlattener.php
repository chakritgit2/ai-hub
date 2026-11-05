<?php

declare(strict_types=1);

namespace ConsoleApi\Services;

/**
 * Turns Tempo's raw OTLP-shaped `GET /api/traces/{id}` response into the small, stable
 * shape the console's Trace tab waterfall (web/src/routes/runs/[id]/+page.svelte) actually
 * needs - PRD §6.9. Pure logic, no DI/DB/HTTP - kept out of RunsController specifically so
 * it's unit-testable (this codebase has no controller-level PHPUnit harness to extend).
 *
 * Tempo's response wraps spans in `resourceSpans[].scopeSpans[].spans[]`, or sometimes
 * `batches[].scopeSpans[].spans[]` depending on Tempo version/content negotiation (not
 * pinned down in this repo - both top-level keys are OTLP-valid, so both are accepted).
 * Each span's `attributes` is a list of `{key, value: {stringValue|intValue|doubleValue|
 * boolValue: ...}}` typed-union entries, not a plain object - unwrapped into a flat scalar
 * map here so the frontend never has to know that shape exists.
 */
class TempoTraceFlattener
{
    /**
     * @param array<string, mixed> $tempoResponse
     * @return array{trace_id: string, spans: array<int, array<string, mixed>>}
     */
    public function flatten(string $traceId, array $tempoResponse): array
    {
        $rawSpans = $this->extractRawSpans($tempoResponse);

        $spans = array_map(fn (array $span) => $this->flattenSpan($span), $rawSpans);

        if ($spans === []) {
            return ['trace_id' => $traceId, 'spans' => []];
        }

        // The trace's true start is the minimum start time across ALL spans, not
        // necessarily the first span Tempo happened to list first - array order here is
        // not execution order.
        $traceStartNano = min(array_column($spans, '_start_unix_nano'));

        $spans = array_map(function (array $span) use ($traceStartNano): array {
            // PHP's `/` returns an int, not a float, when both operands are int and
            // divide evenly (e.g. the trace's own earliest span: 0 / 1_000_000 === 0, an
            // int) - forced to float so this field's JSON type never flips between a
            // whole-number span and a fractional one.
            $span['start_ms_relative'] = (float) (($span['_start_unix_nano'] - $traceStartNano) / 1_000_000);
            unset($span['_start_unix_nano'], $span['_end_unix_nano']);

            return $span;
        }, $spans);

        usort($spans, fn (array $a, array $b) => $a['start_ms_relative'] <=> $b['start_ms_relative']);

        return ['trace_id' => $traceId, 'spans' => array_values($spans)];
    }

    /**
     * @param array<string, mixed> $tempoResponse
     * @return array<int, array<string, mixed>>
     */
    private function extractRawSpans(array $tempoResponse): array
    {
        $resourceSpans = $tempoResponse['resourceSpans'] ?? $tempoResponse['batches'] ?? [];

        $spans = [];
        foreach ($resourceSpans as $resourceSpan) {
            foreach ($resourceSpan['scopeSpans'] ?? [] as $scopeSpan) {
                foreach ($scopeSpan['spans'] ?? [] as $span) {
                    $spans[] = $span;
                }
            }
        }

        return $spans;
    }

    /**
     * @param array<string, mixed> $span
     * @return array<string, mixed>
     */
    private function flattenSpan(array $span): array
    {
        $startNano = (int) ($span['startTimeUnixNano'] ?? 0);
        $endNano = (int) ($span['endTimeUnixNano'] ?? $startNano);

        return [
            'span_id' => (string) ($span['spanId'] ?? ''),
            'parent_span_id' => isset($span['parentSpanId']) && $span['parentSpanId'] !== ''
                ? (string) $span['parentSpanId']
                : null,
            'name' => (string) ($span['name'] ?? ''),
            'duration_ms' => (float) (max(0, $endNano - $startNano) / 1_000_000),
            'attributes' => $this->flattenAttributes($span['attributes'] ?? []),
            'status' => $this->flattenStatus($span['status'] ?? null),
            // Consumed by flatten() to compute start_ms_relative against the trace-wide
            // minimum, then stripped before the response goes out.
            '_start_unix_nano' => $startNano,
            '_end_unix_nano' => $endNano,
        ];
    }

    /**
     * @param array<int, array{key?: string, value?: array<string, mixed>}> $attributes
     * @return array<string, string|int|float|bool>
     */
    private function flattenAttributes(array $attributes): array
    {
        $flat = [];
        foreach ($attributes as $attribute) {
            $key = $attribute['key'] ?? null;
            $value = $attribute['value'] ?? null;
            if ($key === null || !is_array($value)) {
                continue;
            }

            if (array_key_exists('stringValue', $value)) {
                $flat[$key] = (string) $value['stringValue'];
            } elseif (array_key_exists('intValue', $value)) {
                // OTLP JSON encodes int64 as a string (avoids JS/JSON precision loss).
                $flat[$key] = (int) $value['intValue'];
            } elseif (array_key_exists('doubleValue', $value)) {
                $flat[$key] = (float) $value['doubleValue'];
            } elseif (array_key_exists('boolValue', $value)) {
                $flat[$key] = (bool) $value['boolValue'];
            }
            // arrayValue/kvlistValue: skipped - none of this run's known span attributes
            // (gen_ai.*, db.*, http.*) use them, and the waterfall only ever shows scalars.
        }

        return $flat;
    }

    /**
     * OTLP Status.code: 0 = UNSET, 1 = OK, 2 = ERROR.
     *
     * @param array{code?: int}|null $status
     */
    private function flattenStatus(?array $status): string
    {
        return match ($status['code'] ?? 0) {
            1 => 'ok',
            2 => 'error',
            default => 'unset',
        };
    }
}
