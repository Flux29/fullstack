"""The relation extractor: routes, tasks, models, test edges, and the tool registry.

Synthetic trees pin each mechanism — prefix resolution through the v1 registry, the
un-included router, decorator shapes bare and called, the registry-as-data rule for Google
tools — and the real-tree tests pin the properties impact analysis will rely on: every
route path fully qualified, the known task and model surfaces present, zero unknowns.
"""

from __future__ import annotations

from pathlib import Path

from repo_governance.config import Context
from repo_governance.extractors.relations import Relations, build_relations, routes_to_edges
from repo_governance.io_atomic import canonical_json

V1 = "backend/app/api/routes/v1"


def _write(root: Path, relative: str, text: str = "") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _relations(minimal_repo: Path) -> Relations:
    return build_relations(Context.discover(minimal_repo))


def test_route_paths_are_qualified_through_the_v1_registry(minimal_repo: Path) -> None:
    _write(
        minimal_repo,
        f"{V1}/__init__.py",
        "v1_router.include_router(sessions.router, prefix=\"/sessions\", tags=[\"sessions\"])\n"
        "v1_router.include_router(health.router, tags=[\"health\"])\n",
    )
    _write(
        minimal_repo,
        f"{V1}/sessions.py",
        '@router.get("", response_model=None)\nasync def list_sessions(): ...\n'
        '@router.delete("/{session_id}")\nasync def logout(session_id): ...\n',
    )
    _write(minimal_repo, f"{V1}/health.py", '@router.get("/health")\nasync def health(): ...\n')

    relations = _relations(minimal_repo)
    entries = {(route.method, route.path, route.module) for route in relations.api_routes}
    assert ("GET", "/api/v1/sessions", "app.api.routes.v1.sessions") in entries
    assert ("DELETE", "/api/v1/sessions/{session_id}", "app.api.routes.v1.sessions") in entries
    assert ("GET", "/api/v1/health", "app.api.routes.v1.health") in entries


def test_websocket_decorator_is_a_route_with_websocket_method(minimal_repo: Path) -> None:
    _write(minimal_repo, f"{V1}/__init__.py", "v1_router.include_router(agent.router, tags=[\"agent\"])\n")
    _write(minimal_repo, f"{V1}/agent.py", '@router.websocket("/ws/agent")\nasync def ws(): ...\n')

    relations = _relations(minimal_repo)
    assert ("WEBSOCKET", "/api/v1/ws/agent") in {(r.method, r.path) for r in relations.api_routes}


def test_module_missing_from_the_registry_is_recorded_not_misnamed(minimal_repo: Path) -> None:
    _write(minimal_repo, f"{V1}/__init__.py", "v1_router.include_router(health.router)\n")
    _write(minimal_repo, f"{V1}/orphan.py", '@router.get("/x")\nasync def x(): ...\n')

    relations = _relations(minimal_repo)
    assert any("orphan.py" in entry and "registry" in entry for entry in relations.unknowns)


def test_non_literal_route_path_is_an_unknown(minimal_repo: Path) -> None:
    _write(minimal_repo, f"{V1}/__init__.py", "v1_router.include_router(dyn.router)\n")
    _write(minimal_repo, f"{V1}/dyn.py", '@router.get(PATH)\nasync def x(): ...\n')

    relations = _relations(minimal_repo)
    assert relations.api_routes == ()
    assert any("non-literal route path" in entry for entry in relations.unknowns)


def test_broker_task_decorator_bare_and_called_are_both_tasks(minimal_repo: Path) -> None:
    _write(
        minimal_repo,
        "backend/app/worker/tasks/rag_tasks.py",
        "@broker.task\nasync def ingest_document_task(): ...\n"
        '@broker.task(schedule=[{"cron": "0 3 * * *"}])\nasync def nightly_sync(): ...\n',
    )

    relations = _relations(minimal_repo)
    names = {(task.name, task.module) for task in relations.tasks}
    assert ("ingest_document_task", "app.worker.tasks.rag_tasks") in names
    assert ("nightly_sync", "app.worker.tasks.rag_tasks") in names


def test_orm_models_are_found_with_tablename(minimal_repo: Path) -> None:
    _write(
        minimal_repo,
        "backend/app/db/models/user.py",
        'class User(Base, TimestampMixin):\n    __tablename__ = "users"\n',
    )
    _write(minimal_repo, "backend/app/db/models/helper.py", "class NotAModel:\n    pass\n")

    relations = _relations(minimal_repo)
    assert [(m.name, m.table) for m in relations.models] == [("User", "users")]


def test_test_imports_of_app_modules_become_test_edges(minimal_repo: Path) -> None:
    _write(minimal_repo, "backend/app/services/user.py")
    _write(
        minimal_repo,
        "backend/tests/test_services.py",
        "from app.services.user import UserService\nimport app.services.missing\n",
    )

    relations = _relations(minimal_repo)
    assert ("backend/tests/test_services.py", "app.services.user") in relations.test_edges
    assert any("app.services.missing" in entry for entry in relations.unknowns)


def test_google_registry_is_read_as_a_table(minimal_repo: Path) -> None:
    _write(
        minimal_repo,
        "backend/app/agents/google_apis/products.py",
        '_product(\n    "drive",\n    DRIVE_URL,\n    (DRIVE_SCOPE,),\n'
        '    (\n        ("search_files", "Search."),\n        ("upload_file", "Upload."),\n    ),\n)\n',
    )

    relations = _relations(minimal_repo)
    assert relations.tools == ({"kind": "drive", "tools": ["search_files", "upload_file"]},)


def test_unparseable_file_is_an_unknown_not_an_absence(minimal_repo: Path) -> None:
    _write(minimal_repo, f"{V1}/__init__.py", "")
    _write(minimal_repo, f"{V1}/broken.py", "def broken(:\n")

    relations = _relations(minimal_repo)
    assert any("broken.py" in entry for entry in relations.unknowns)


def test_real_tree_routes_are_fully_qualified(real_context: Context) -> None:
    relations = build_relations(real_context)
    assert len(relations.api_routes) > 30
    assert all(route.path.startswith("/api/v1") for route in relations.api_routes), [
        route for route in relations.api_routes if not route.path.startswith("/api/v1")
    ]


def test_real_tree_knows_the_task_model_and_tool_surfaces(real_context: Context) -> None:
    relations = build_relations(real_context)
    assert {task.module for task in relations.tasks} >= {
        "app.worker.tasks.rag_tasks",
        "app.worker.tasks.schedules",
    }
    assert len(relations.models) >= 10
    assert "users" in {model.table for model in relations.models}
    kinds = {entry["kind"] for entry in relations.tools}
    assert "drive" in kinds and len(kinds) >= 4, kinds
    assert len(relations.test_edges) > 20


def test_real_tree_has_no_unknowns(real_context: Context) -> None:
    """Every relation surface parses clean today; a future unknown is a real finding."""
    assert build_relations(real_context).unknowns == ()


def test_real_tree_routes_to_edges_land_in_services(real_context: Context) -> None:
    relations = build_relations(real_context)
    edges = routes_to_edges(real_context, relations)
    assert edges, "route modules import no services? the layering rule says otherwise"
    assert all(dst.startswith("app.services") for _, dst in edges)


def test_serialization_is_deterministic(real_context: Context) -> None:
    first = canonical_json(build_relations(real_context).as_payload())
    second = canonical_json(build_relations(real_context).as_payload())
    assert first == second
