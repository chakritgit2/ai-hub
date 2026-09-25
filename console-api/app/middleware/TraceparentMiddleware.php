<?php

declare(strict_types=1);

namespace ConsoleApi\Middleware;

use Phalcon\Events\Event;
use Phalcon\Mvc\Dispatcher;

/**
 * Reads the incoming W3C `traceparent` header, or generates a new one when it is
 * missing/malformed, and stores it for downstream propagation to ai-runtime via
 * RuntimeClient (PRD §6.9 — "console-api propagates traceparent to ai-runtime and
 * on to HTTP Tools so traces span systems").
 */
class TraceparentMiddleware
{
    private static ?string $current = null;

    public function beforeDispatch(Event $event, Dispatcher $dispatcher): bool
    {
        $incoming = $_SERVER['HTTP_TRACEPARENT'] ?? null;

        $traceparent = $this->isValid($incoming) ? $incoming : $this->generate();

        self::$current = $traceparent;
        $dispatcher->setParam('traceparent', $traceparent);

        return true;
    }

    /**
     * Convenience accessor for services (e.g. RuntimeClient) that need the current
     * request's traceparent but aren't wired through the dispatcher's params.
     */
    public static function current(): ?string
    {
        return self::$current;
    }

    private function isValid(?string $value): bool
    {
        if ($value === null) {
            return false;
        }

        // version-traceId-parentId-flags, per the W3C Trace Context spec.
        return (bool) preg_match('/^[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$/', $value);
    }

    private function generate(): string
    {
        $traceId = bin2hex(random_bytes(16));
        $spanId = bin2hex(random_bytes(8));

        return sprintf('00-%s-%s-01', $traceId, $spanId);
    }
}
