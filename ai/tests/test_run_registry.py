import uuid

from dynamiq.types.cancellation import CancellationToken

from app.services import run_registry


def test_request_cancel_returns_false_for_an_unregistered_run() -> None:
    assert run_registry.request_cancel(str(uuid.uuid4()), company_id="c1", agent_version_id="v1") is False


def test_request_cancel_signals_the_token_for_a_matching_run() -> None:
    run_id = str(uuid.uuid4())
    token = CancellationToken()
    run_registry.register(run_id, company_id="c1", agent_version_id="v1", token=token)
    try:
        assert run_registry.request_cancel(run_id, company_id="c1", agent_version_id="v1") is True
        assert token.is_canceled is True
    finally:
        run_registry.unregister(run_id)


def test_request_cancel_is_false_and_does_not_signal_for_a_different_company() -> None:
    """A guessed run_id for someone else's run must behave exactly like an unknown one
    (PRD §12) - not reveal that it exists, and must never actually cancel it."""
    run_id = str(uuid.uuid4())
    token = CancellationToken()
    run_registry.register(run_id, company_id="c1", agent_version_id="v1", token=token)
    try:
        assert run_registry.request_cancel(run_id, company_id="other-company", agent_version_id="v1") is False
        assert token.is_canceled is False
    finally:
        run_registry.unregister(run_id)


def test_request_cancel_is_false_and_does_not_signal_for_a_different_agent_version() -> None:
    run_id = str(uuid.uuid4())
    token = CancellationToken()
    run_registry.register(run_id, company_id="c1", agent_version_id="v1", token=token)
    try:
        assert run_registry.request_cancel(run_id, company_id="c1", agent_version_id="other-version") is False
        assert token.is_canceled is False
    finally:
        run_registry.unregister(run_id)


def test_unregister_makes_a_later_cancel_request_a_no_op() -> None:
    run_id = str(uuid.uuid4())
    token = CancellationToken()
    run_registry.register(run_id, company_id="c1", agent_version_id="v1", token=token)
    run_registry.unregister(run_id)

    assert run_registry.request_cancel(run_id, company_id="c1", agent_version_id="v1") is False
    assert token.is_canceled is False


def test_unregister_of_an_unknown_run_id_is_a_silent_no_op() -> None:
    run_registry.unregister(str(uuid.uuid4()))


def test_has_finished_is_false_for_a_run_id_never_seen() -> None:
    assert run_registry.has_finished(str(uuid.uuid4())) is False


def test_has_finished_is_true_after_unregister() -> None:
    """Lets `POST /ai/v1/runs/{id}/cancel` (app.main_runtime) fail fast once a run has
    already finished, instead of waiting out its full registration-grace window."""
    run_id = str(uuid.uuid4())
    run_registry.register(run_id, company_id="c1", agent_version_id="v1", token=CancellationToken())
    run_registry.unregister(run_id)

    assert run_registry.has_finished(run_id) is True


def test_has_finished_is_false_again_once_the_run_id_is_registered_again() -> None:
    """A re-used run_id (shouldn't normally happen with uuid4, but cheap to guarantee)
    must not be treated as already-finished the moment it starts running again."""
    run_id = str(uuid.uuid4())
    run_registry.register(run_id, company_id="c1", agent_version_id="v1", token=CancellationToken())
    run_registry.unregister(run_id)
    run_registry.register(run_id, company_id="c1", agent_version_id="v1", token=CancellationToken())

    assert run_registry.has_finished(run_id) is False
