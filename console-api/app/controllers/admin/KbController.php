<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Http\Response;

/**
 * /kb — x-phase: 2 in the OpenAPI contract (PRD §6.6, §8.1 `knowledge_bases`/`kb_documents`
 * tables; vector tables live per-KB in `runtime.kbv_{company_id}_{kb_id}`).
 */
class KbController extends ControllerBase
{
    public function listKnowledgeBases(): Response
    {
        return $this->notImplemented();
    }

    public function createKnowledgeBase(): Response
    {
        return $this->notImplemented();
    }

    public function importKnowledgeBaseFiles(string $id): Response
    {
        return $this->notImplemented();
    }

    public function exportKnowledgeBase(string $id): Response
    {
        return $this->notImplemented();
    }

    public function searchKnowledgeBase(string $id): Response
    {
        return $this->notImplemented();
    }
}
