"""`runtime.tool_approvals` repository (PRD §6.5/§8.1) - first tests for this module;
neither the pre-existing decide half (`get_approval`/`decide_approval`) nor the new
create/wait/gate half had any coverage before this feature."""
import uuid

import pytest

from app.core.config import get_settings
from app.services import approvals as approvals_module
from app.services.approvals import (
    ApprovalAlreadyDecidedError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
    ApprovalRejectedError,
    WriteToolsNotAllowedError,
    create_pending_approval,
    decide_approval,
    enforce_write_tool_gate,
    get_approval,
    wait_for_decision,
)

from .markers import requires_postgres

pytestmark = [requires_postgres]


def _shrink_approval_timeout(monkeypatch, *, timeout_seconds: float, poll_interval_seconds: float = 0.05) -> None:
    """`wait_for_decision` polls every `_POLL_INTERVAL_SECONDS` against
    `Settings.APPROVAL_TIMEOUT_SECONDS` (5 minutes in production) - both are shrunk to
    fractions of a second so timeout tests run fast and deterministically instead of
    actually waiting the real 5-minute PRD window."""
    settings = get_settings()
    monkeypatch.setattr(settings, "APPROVAL_TIMEOUT_SECONDS", timeout_seconds)
    monkeypatch.setattr(approvals_module, "_POLL_INTERVAL_SECONDS", poll_interval_seconds)


async def test_create_pending_approval_inserts_a_pending_row(company_ids) -> None:
    company_id = company_ids()
    run_id, tool_id = str(uuid.uuid4()), str(uuid.uuid4())

    approval_id = await create_pending_approval(company_id, run_id, tool_id, request={"input": {"amount": 100}})

    approval = await get_approval(company_id, approval_id)
    assert approval is not None
    assert approval.run_id == run_id
    assert approval.tool_id == tool_id
    assert approval.request == {"input": {"amount": 100}}
    assert approval.status == "pending"
    assert approval.decided_by is None


async def test_get_approval_returns_none_for_an_unknown_id(company_ids) -> None:
    company_id = company_ids()

    assert await get_approval(company_id, str(uuid.uuid4())) is None


async def test_decide_approval_flips_a_pending_row_to_approved(company_ids) -> None:
    company_id = company_ids()
    approval_id = await create_pending_approval(
        company_id, str(uuid.uuid4()), str(uuid.uuid4()), request={}
    )

    approval = await decide_approval(company_id, approval_id, "approved", decided_by="user-1")

    assert approval.status == "approved"
    assert approval.decided_by == "user-1"
    assert approval.decided_at is not None


async def test_decide_approval_raises_not_found_for_an_unknown_id(company_ids) -> None:
    company_id = company_ids()

    with pytest.raises(ApprovalNotFoundError):
        await decide_approval(company_id, str(uuid.uuid4()), "approved", decided_by="user-1")


async def test_decide_approval_rejects_a_second_decision_on_the_same_approval(company_ids) -> None:
    """A double-click, or the console and the app racing, must not silently overwrite the
    first decision - the second call sees the row's real current status instead."""
    company_id = company_ids()
    approval_id = await create_pending_approval(
        company_id, str(uuid.uuid4()), str(uuid.uuid4()), request={}
    )
    await decide_approval(company_id, approval_id, "approved", decided_by="user-1")

    with pytest.raises(ApprovalAlreadyDecidedError) as exc_info:
        await decide_approval(company_id, approval_id, "rejected", decided_by="user-2")

    assert exc_info.value.status == "approved"


async def test_wait_for_decision_returns_approved_once_a_human_decides_while_polling(
    monkeypatch, company_ids
) -> None:
    _shrink_approval_timeout(monkeypatch, timeout_seconds=5)
    company_id = company_ids()
    approval_id = await create_pending_approval(
        company_id, str(uuid.uuid4()), str(uuid.uuid4()), request={}
    )
    await decide_approval(company_id, approval_id, "approved", decided_by="user-1")

    assert await wait_for_decision(company_id, approval_id) == "approved"


async def test_wait_for_decision_returns_rejected(monkeypatch, company_ids) -> None:
    _shrink_approval_timeout(monkeypatch, timeout_seconds=5)
    company_id = company_ids()
    approval_id = await create_pending_approval(
        company_id, str(uuid.uuid4()), str(uuid.uuid4()), request={}
    )
    await decide_approval(company_id, approval_id, "rejected", decided_by="user-1")

    assert await wait_for_decision(company_id, approval_id) == "rejected"


async def test_wait_for_decision_expires_a_still_pending_row_after_the_timeout(
    monkeypatch, company_ids
) -> None:
    """PRD §6.5: "otherwise rejected" after the 5-minute window - modeled here as a real,
    row-level `expired` status (distinct from a human's explicit `rejected`)."""
    _shrink_approval_timeout(monkeypatch, timeout_seconds=0.1)
    company_id = company_ids()
    approval_id = await create_pending_approval(
        company_id, str(uuid.uuid4()), str(uuid.uuid4()), request={}
    )

    status = await wait_for_decision(company_id, approval_id)

    assert status == "expired"
    approval = await get_approval(company_id, approval_id)
    assert approval.status == "expired"
    assert approval.decided_by == "system:timeout"


async def test_enforce_write_tool_gate_rejects_outright_when_the_deployment_disallows_write_tools(
    monkeypatch, company_ids
) -> None:
    """PRD: a write tool "must be allowed by the deployment" - checked before a `pending`
    row is ever created, since there's nothing for a human to decide."""
    company_id = company_ids()
    run_id, tool_id = str(uuid.uuid4()), str(uuid.uuid4())
    create_calls: list[tuple] = []

    async def _fail_if_called(*args, **kwargs):
        create_calls.append(args)
        raise AssertionError("create_pending_approval must not be called when allow_write_tools is False")

    monkeypatch.setattr(approvals_module, "create_pending_approval", _fail_if_called)

    with pytest.raises(WriteToolsNotAllowedError):
        await enforce_write_tool_gate(company_id, run_id, tool_id, allow_write_tools=False, request={})

    assert create_calls == []


async def test_enforce_write_tool_gate_returns_normally_once_approved(monkeypatch, company_ids) -> None:
    _shrink_approval_timeout(monkeypatch, timeout_seconds=5)
    company_id = company_ids()
    run_id, tool_id = str(uuid.uuid4()), str(uuid.uuid4())

    async def _approve_it_shortly_after_creation(*args, **kwargs):
        approval_id = await create_pending_approval(*args, **kwargs)
        await decide_approval(company_id, approval_id, "approved", decided_by="user-1")
        return approval_id

    monkeypatch.setattr(approvals_module, "create_pending_approval", _approve_it_shortly_after_creation)

    await enforce_write_tool_gate(company_id, run_id, tool_id, allow_write_tools=True, request={})


async def test_enforce_write_tool_gate_raises_rejected_when_a_human_rejects(monkeypatch, company_ids) -> None:
    _shrink_approval_timeout(monkeypatch, timeout_seconds=5)
    company_id = company_ids()
    run_id, tool_id = str(uuid.uuid4()), str(uuid.uuid4())

    async def _reject_it_shortly_after_creation(*args, **kwargs):
        approval_id = await create_pending_approval(*args, **kwargs)
        await decide_approval(company_id, approval_id, "rejected", decided_by="user-1")
        return approval_id

    monkeypatch.setattr(approvals_module, "create_pending_approval", _reject_it_shortly_after_creation)

    with pytest.raises(ApprovalRejectedError):
        await enforce_write_tool_gate(company_id, run_id, tool_id, allow_write_tools=True, request={})


async def test_enforce_write_tool_gate_raises_expired_when_nobody_decides_in_time(
    monkeypatch, company_ids
) -> None:
    _shrink_approval_timeout(monkeypatch, timeout_seconds=0.1)
    company_id = company_ids()
    run_id, tool_id = str(uuid.uuid4()), str(uuid.uuid4())

    with pytest.raises(ApprovalExpiredError):
        await enforce_write_tool_gate(company_id, run_id, tool_id, allow_write_tools=True, request={})


async def test_enforce_write_tool_gate_calls_on_pending_with_the_real_approval_id_before_waiting(
    monkeypatch, company_ids
) -> None:
    """PRD §6.5's `approval_required` SSE event - pushed by
    `app.integrations.write_tool_gate.enforce_write_tool_gate_or_raise`'s own `on_pending`
    closure, but this module (dynamiq/streaming-agnostic by design) only needs to prove it
    calls back with the right id at the right moment: after the row exists, before the
    wait starts."""
    _shrink_approval_timeout(monkeypatch, timeout_seconds=5)
    company_id = company_ids()
    run_id, tool_id = str(uuid.uuid4()), str(uuid.uuid4())
    seen: list[str] = []

    def _on_pending(approval_id: str) -> None:
        seen.append(approval_id)

    async def _approve_it_shortly_after_creation(*args, **kwargs):
        approval_id = await create_pending_approval(*args, **kwargs)
        await decide_approval(company_id, approval_id, "approved", decided_by="user-1")
        return approval_id

    monkeypatch.setattr(approvals_module, "create_pending_approval", _approve_it_shortly_after_creation)

    await enforce_write_tool_gate(
        company_id, run_id, tool_id, allow_write_tools=True, request={}, on_pending=_on_pending
    )

    assert len(seen) == 1
    approval = await get_approval(company_id, seen[0])
    assert approval is not None
    assert approval.run_id == run_id
    assert approval.tool_id == tool_id


async def test_enforce_write_tool_gate_never_calls_on_pending_when_disallowed(monkeypatch, company_ids) -> None:
    company_id = company_ids()
    run_id, tool_id = str(uuid.uuid4()), str(uuid.uuid4())
    called = False

    def _on_pending(_approval_id: str) -> None:
        nonlocal called
        called = True

    with pytest.raises(WriteToolsNotAllowedError):
        await enforce_write_tool_gate(
            company_id, run_id, tool_id, allow_write_tools=False, request={}, on_pending=_on_pending
        )

    assert called is False
