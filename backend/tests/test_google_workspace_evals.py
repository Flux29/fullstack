from evals.google_workspace_tools import (
    GOOGLE_WORKSPACE_DETERMINISM,
    GOOGLE_WORKSPACE_LOADOUT,
    GOOGLE_WORKSPACE_NEGATIVE,
    GOOGLE_WORKSPACE_ROUTING,
    evaluate_determinism,
    evaluate_loadout,
    evaluate_route,
)


def test_google_workspace_pydantic_eval_suite_passes():
    report = GOOGLE_WORKSPACE_ROUTING.evaluate_sync(evaluate_route, progress=False)
    assert report.failures == []


def test_google_workspace_loadout_covers_required_tools():
    report = GOOGLE_WORKSPACE_LOADOUT.evaluate_sync(evaluate_loadout, progress=False)
    assert report.failures == []
    _assert_all_assertions_passed(report)


def test_google_workspace_loadout_loads_nothing_without_a_google_signal():
    report = GOOGLE_WORKSPACE_NEGATIVE.evaluate_sync(evaluate_loadout, progress=False)
    assert report.failures == []
    _assert_all_assertions_passed(report)


def test_google_workspace_loadout_serialization_is_deterministic():
    report = GOOGLE_WORKSPACE_DETERMINISM.evaluate_sync(evaluate_determinism, progress=False)
    assert report.failures == []
    _assert_all_assertions_passed(report)


def _assert_all_assertions_passed(report) -> None:
    """A case that raises shows up in ``failures``; a failing assertion does not.

    ``evaluate_sync`` records evaluator verdicts as assertions on each case, so
    without this check a dataset whose evaluators all return False would still
    report an empty failure list and the test would pass vacuously.
    """
    failed = [
        (case.name, name)
        for case in report.cases
        for name, assertion in case.assertions.items()
        if not assertion.value
    ]
    assert failed == [], f"failed assertions: {failed}"
