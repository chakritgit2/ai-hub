<?php

declare(strict_types=1);

namespace ConsoleApi\Controllers\Admin;

use ConsoleApi\Controllers\ControllerBase;
use Phalcon\Http\Response;

/**
 * /datasets — x-phase: 3 in the OpenAPI contract (PRD §8.1 `datasets`/`dataset_items` tables).
 */
class DatasetsController extends ControllerBase
{
    public function listDatasets(): Response
    {
        return $this->notImplemented();
    }

    public function createDataset(): Response
    {
        return $this->notImplemented();
    }
}
