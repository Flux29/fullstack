from evals.google_workspace_tools import GOOGLE_WORKSPACE_ROUTING, evaluate_route


def test_google_workspace_pydantic_eval_suite_passes():
    report = GOOGLE_WORKSPACE_ROUTING.evaluate_sync(evaluate_route, progress=False)
    assert report.failures == []
