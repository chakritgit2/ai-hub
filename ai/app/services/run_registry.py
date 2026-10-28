"""In-process registry letting `POST /ai/v1/runs/{id}/cancel` (app.main_runtime) signal a
still-running agent run's own dynamiq `CancellationToken` (app.services.runtime) - the
Playground "stop generating" button reaches the real ReAct loop via dynamiq's own built-in
cooperative cancellation (`dynamiq.types.cancellation.check_cancellation`, called at dozens
of checkpoints throughout `dynamiq.nodes.agents.agent`), no callback hack needed.

Deliberately in-memory, not Redis/DB-backed - correct only because ai-runtime is
single-replica today (`deploy/k8s/base/ai-runtime-deployment.yaml`: `replicas: 1`, no
overlay changes it yet - production's intended `ai-runtime: 2` is still just a TODO in
`deploy/k8s/overlays/production/kustomization.yaml`). If that TODO ever ships, a cancel
request can land on a different pod than the one actually running the target run and
silently no-op - this needs a shared (Redis pub/sub or DB-polled flag) signal before that
happens, the same problem `app.services.eval_runs` solves for free via a Postgres row
instead of in-process state.
"""
from collections import OrderedDict

from dynamiq.types.cancellation import CancellationToken

_active: dict[str, tuple[str, str, CancellationToken]] = {}

# Bounded history of run_ids that were registered here and have since finished (success,
# failure, or already cancelled) - lets `request_cancel`'s caller (app.main_runtime's
# `cancelRun` grace-period retry) tell "not registered *yet*" (worth continuing to poll -
# a client can legitimately call cancel the instant it sees `run_started`, before this
# run has even reached app.services.runtime's own registration point) apart from "already
# over" (stop waiting out the rest of the window - there's nothing left to catch). A plain
# growing set would leak memory forever; capped at `_MAX_FINISHED_HISTORY` and evicted
# oldest-first instead.
_MAX_FINISHED_HISTORY = 500
_finished: "OrderedDict[str, None]" = OrderedDict()


def register(run_id: str, *, company_id: str, agent_version_id: str, token: CancellationToken) -> None:
    _active[run_id] = (company_id, agent_version_id, token)
    _finished.pop(run_id, None)


def unregister(run_id: str) -> None:
    _active.pop(run_id, None)
    _finished[run_id] = None
    if len(_finished) > _MAX_FINISHED_HISTORY:
        _finished.popitem(last=False)


def has_finished(run_id: str) -> bool:
    """True once a run that was registered here has since completed and been
    unregistered (see `_finished`'s own docstring above) - always False for a run_id this
    process never saw at all, which is indistinguishable from "not registered yet" here,
    exactly as intended."""
    return run_id in _finished


def request_cancel(run_id: str, *, company_id: str, agent_version_id: str) -> bool:
    """Signals the run's token iff it's still active *and* belongs to the caller's own
    company+agent version - a guessed run_id for someone else's run must behave exactly
    like an unknown one (PRD §12), not reveal that it exists. Returns False (never
    raises) for "nothing to cancel here" - the caller decides the HTTP status."""
    entry = _active.get(run_id)
    if entry is None or entry[0] != company_id or entry[1] != agent_version_id:
        return False
    entry[2].cancel()
    return True
