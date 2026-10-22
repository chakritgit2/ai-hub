"""Shared human-approval gate translation for `access_level: write` tools (PRD §6.5/§8.1).

`SafeHttpApiCall` (`safe_http_tool.py`) and `_SafeBuiltinSearchTool` (`safe_builtin_tools.py`)
both need the exact same thing here: call `app.services.approvals.enforce_write_tool_gate`
and turn its three outcome exceptions into one consistent, recoverable
`ToolExecutionException` message - `app.services.approvals` itself stays dynamiq-agnostic
(same reasoning it already documents for not importing dynamiq at all), so this one function
is the single place that bridges the two.
"""
from dynamiq.nodes.agents.exceptions import ToolExecutionException

from app.services.approvals import (
    ApprovalExpiredError,
    ApprovalRejectedError,
    WriteToolsNotAllowedError,
    enforce_write_tool_gate,
)


async def enforce_write_tool_gate_or_raise(
    tool_name: str,
    company_id: str,
    run_id: str | None,
    tool_id: str,
    allow_write_tools: bool,
    request: dict,
) -> None:
    """Returns normally only when a human approved the call. `request` must already be
    built from the LLM's own tool-call input alone (e.g. `input_data.model_dump(mode=
    "json")`) - never from a fully-built outbound request, which could carry a
    `service`-mode secret or a `delegated` JWT in its headers that a company's own console
    users (who can read `runtime.tool_approvals`) must never see. Callers are responsible
    for calling this *before* any such secret/JWT is attached."""
    try:
        await enforce_write_tool_gate(company_id, run_id or "", tool_id, allow_write_tools, request=request)
    except WriteToolsNotAllowedError as exc:
        raise ToolExecutionException(
            f"Tool {tool_name!r} requires write access, which this deployment does not allow.",
            recoverable=True,
        ) from exc
    except ApprovalRejectedError as exc:
        raise ToolExecutionException(
            f"Tool {tool_name!r} call was rejected by a human reviewer.", recoverable=True
        ) from exc
    except ApprovalExpiredError as exc:
        raise ToolExecutionException(
            f"Tool {tool_name!r} call timed out waiting for human approval.", recoverable=True
        ) from exc
