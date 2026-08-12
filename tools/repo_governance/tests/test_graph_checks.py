"""The graph-backed boundary checks and the ORM pairing heuristic.

A check that never fires is worse than no check, so each rule's test introduces the exact
condition it exists to catch. Real-tree assertions are property-pinned, not count-pinned:
when someone adds the rag facade, the necessary test update is the desired signal, while a
pinned count would break on every unrelated backend import.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repo_governance.checks import CheckScope
from repo_governance.checks.compatibility import check_orm_migration_pairing
from repo_governance.checks.graph import (
    check_broken_site_chains,
    check_forbidden_dependencies,
    check_layering,
    check_thick_domain_facades,
)
from repo_governance.config import Context


def _write(root: Path, relative: str, text: str = "") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _scope(root: Path) -> CheckScope:
    return CheckScope(ctx=Context.discover(root))


# --- layering ----------------------------------------------------------------------------


def test_layering_check_passes_on_current_tree(real_context: Context) -> None:
    assert check_layering(CheckScope(ctx=real_context)) == []


def test_layering_check_flags_route_importing_repository(minimal_repo: Path) -> None:
    _write(
        minimal_repo,
        "backend/app/api/routes/v1/users.py",
        "from app.repositories import user as user_repo\n",
    )
    _write(minimal_repo, "backend/app/repositories/user.py")

    issues = check_layering(_scope(minimal_repo))
    assert len(issues) == 1
    assert issues[0].path == "backend/app/api/routes/v1/users.py"
    assert "app.repositories.user" in (issues[0].evidence or "")


def test_layering_check_flags_deferred_route_import_of_repository(minimal_repo: Path) -> None:
    """A function-local import in a route is still a call path."""
    _write(
        minimal_repo,
        "backend/app/api/routes/v1/users.py",
        "def handler():\n    from app.repositories import user\n    return user\n",
    )
    _write(minimal_repo, "backend/app/repositories/user.py")

    assert len(check_layering(_scope(minimal_repo))) == 1


def test_layering_check_ignores_type_checking_only_import(minimal_repo: Path) -> None:
    _write(
        minimal_repo,
        "backend/app/api/routes/v1/users.py",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from app.repositories import user\n",
    )
    _write(minimal_repo, "backend/app/repositories/user.py")

    assert check_layering(_scope(minimal_repo)) == []


# --- forbidden dependencies --------------------------------------------------------------


def _annotate(root: Path, relative_dir: str, component: dict) -> None:
    _write(root, f"{relative_dir}/.governance.json", json.dumps(component))


def test_forbidden_dependency_check_flags_edge_into_forbidden_component(minimal_repo: Path) -> None:
    _annotate(
        minimal_repo,
        "backend/app/alpha",
        {
            "id": "alpha-domain",
            "kind": "backend-subsystem",
            "purpose": "test fixture",
            "owns": ["backend/app/alpha/**"],
            "forbidden_dependencies": ["beta-domain"],
        },
    )
    _annotate(
        minimal_repo,
        "backend/app/beta",
        {
            "id": "beta-domain",
            "kind": "backend-subsystem",
            "purpose": "test fixture",
            "owns": ["backend/app/beta/**"],
        },
    )
    _write(minimal_repo, "backend/app/alpha/mod.py", "from app.beta.helper import x\n")
    _write(minimal_repo, "backend/app/beta/helper.py")

    issues = check_forbidden_dependencies(_scope(minimal_repo))
    assert len(issues) == 1
    assert "'alpha-domain'" in issues[0].message and "'beta-domain'" in issues[0].message
    assert issues[0].path == "backend/app/alpha/mod.py"


def test_forbidden_dependency_check_passes_on_current_tree(real_context: Context) -> None:
    """A vacuous pass today: no declared forbidden pair can be expressed as a Python
    import inside backend/app. The check's scope-honesty docstring says so; this pin
    exists so a future Python-expressible violation fails loudly."""
    assert check_forbidden_dependencies(CheckScope(ctx=real_context)) == []


# --- thick-domain facades ----------------------------------------------------------------


def test_facade_check_flags_deep_import_bypassing_facade(minimal_repo: Path) -> None:
    _write(minimal_repo, "backend/app/services/billing/__init__.py", "from app.services.billing.core import Billing\n")
    _write(minimal_repo, "backend/app/services/billing/core.py")
    _write(minimal_repo, "backend/app/services/user.py", "from app.services.billing.core import Billing\n")

    issues = check_thick_domain_facades(_scope(minimal_repo))
    assert len(issues) == 1
    assert issues[0].path == "backend/app/services/user.py"
    assert "past the package root" in issues[0].message


def test_facade_check_flags_namespace_domain_with_no_facade(minimal_repo: Path) -> None:
    _write(minimal_repo, "backend/app/services/rag/config.py")
    _write(minimal_repo, "backend/app/services/user.py", "from app.services.rag.config import settings\n")

    issues = check_thick_domain_facades(_scope(minimal_repo))
    messages = [issue.message for issue in issues]
    assert any("no facade" in message for message in messages)
    assert any("past the package root" in message for message in messages)


def test_facade_check_allows_import_of_package_root(minimal_repo: Path) -> None:
    _write(minimal_repo, "backend/app/services/billing/__init__.py", "from app.services.billing.core import Billing\n")
    _write(minimal_repo, "backend/app/services/billing/core.py")
    _write(minimal_repo, "backend/app/services/user.py", "from app.services.billing import Billing\n")

    assert check_thick_domain_facades(_scope(minimal_repo)) == []


def test_facade_check_skips_importer_covered_by_active_exception(minimal_repo: Path) -> None:
    _write(minimal_repo, "backend/app/services/rag/config.py")
    _write(minimal_repo, "backend/app/services/user.py", "from app.services.rag.config import settings\n")
    _write(
        minimal_repo,
        "governance/manifests/curated/exceptions.json",
        json.dumps(
            {
                "schema_version": 1,
                "exceptions": [
                    {
                        "id": "rag-deep-import-accepted",
                        "statement": "user.py may deep-import rag for now.",
                        "reason": "test fixture",
                        "scope": ["backend/app/services/user.py"],
                        "status": "active",
                    }
                ],
            }
        ),
    )

    issues = check_thick_domain_facades(_scope(minimal_repo))
    assert all(issue.path != "backend/app/services/user.py" for issue in issues)
    assert any("no facade" in issue.message for issue in issues)


def test_facade_check_on_real_tree_accepts_rag_and_reports_email_bypass(real_context: Context) -> None:
    """The ratchet moved on 2026-08-11: services/rag ships its facade and every caller
    imports through it, so a rag finding here means a regression, not a known gap."""
    issues = check_thick_domain_facades(CheckScope(ctx=real_context))
    assert not any(issue.path == "backend/app/services/rag" for issue in issues), (
        "services/rag ships a facade and all callers import through it; a finding here is a regression"
    )
    assert any("'email'" in issue.message for issue in issues), (
        "the email facade is bypassed (auth.py, user.py); the check must report at least one"
    )


# --- broken site chains ------------------------------------------------------------------


def test_broken_chain_exception_excuses_the_call_not_the_page(minimal_repo: Path) -> None:
    """One classified wishlist call must not silence an unclassified break on the same page."""
    _write(
        minimal_repo,
        "frontend/tsconfig.json",
        json.dumps({"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}),
    )
    _write(
        minimal_repo,
        "frontend/src/app/[locale]/settings/page.tsx",
        'await fetch("/api/auth/password/change");\nawait fetch("/api/users/1");\n',
    )
    _write(
        minimal_repo,
        "governance/manifests/generated/interfaces.json",
        json.dumps(
            {
                "proxy_routes": [],
                "frontend_pages": [
                    {"route": "/settings", "file": "frontend/src/app/[locale]/settings/page.tsx"}
                ],
                "websocket": {"endpoint": "/api/v1/ws/agent"},
            }
        ),
    )
    _write(
        minimal_repo,
        "governance/manifests/curated/exceptions.json",
        json.dumps(
            {
                "schema_version": 1,
                "exceptions": [
                    {
                        "id": "wishlist-call-accepted",
                        "statement": "The settings page's password call is wishlist surface.",
                        "reason": "test fixture",
                        "scope": [
                            "frontend/src/app/[locale]/settings/page.tsx calls /api/auth/password/change"
                        ],
                        "status": "active",
                    }
                ],
            }
        ),
    )

    issues = check_broken_site_chains(_scope(minimal_repo))
    assert len(issues) == 1
    assert issues[0].evidence == "/api/users/1"


# --- ORM migration pairing ---------------------------------------------------------------


def _patch_changes(monkeypatch: pytest.MonkeyPatch, changed: list[str] | None) -> None:
    monkeypatch.setattr("repo_governance.checks.process.working_tree_changes", lambda root: changed)


def test_orm_pairing_flags_model_change_without_migration(
    real_context: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_changes(monkeypatch, ["backend/app/db/models/user.py"])
    issues = check_orm_migration_pairing(CheckScope(ctx=real_context))
    assert len(issues) == 1
    assert "no Alembic revision" in issues[0].message


def test_orm_pairing_passes_when_migration_accompanies_model_change(
    real_context: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_changes(
        monkeypatch,
        ["backend/app/db/models/user.py", "backend/alembic/versions/0028_add_flag.py"],
    )
    assert check_orm_migration_pairing(CheckScope(ctx=real_context)) == []


def test_orm_pairing_emits_nothing_when_no_models_changed(
    real_context: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_changes(monkeypatch, ["backend/app/services/user.py"])
    assert check_orm_migration_pairing(CheckScope(ctx=real_context)) == []


def test_orm_pairing_reports_uncertainty_when_git_unavailable(
    real_context: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_changes(monkeypatch, None)
    issues = check_orm_migration_pairing(CheckScope(ctx=real_context))
    assert len(issues) == 1
    assert "not evaluated" in issues[0].message
