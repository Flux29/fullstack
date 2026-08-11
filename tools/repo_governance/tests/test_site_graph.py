"""The site graph: page-to-backend chains, their classifications, and the report.

Synthetic trees pin the three classifications a called path can land in — proxied,
server-side, or unmatched — plus the WebSocket exception flag; the real-tree tests pin
the corpus-level facts reviews will rely on.
"""

from __future__ import annotations

import json
from pathlib import Path

from repo_governance.config import Context
from repo_governance.extractors.ts_imports import _normalize_called
from repo_governance.graph.site import build_boundaries_report, build_site_chains
from repo_governance.io_atomic import canonical_json

TSCONFIG = json.dumps({"compilerOptions": {"paths": {"@/*": ["./src/*"]}}})


def _write(root: Path, relative: str, text: str = "") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _interfaces(root: Path, proxy_routes: list[dict], pages: list[dict]) -> None:
    _write(
        root,
        "governance/manifests/generated/interfaces.json",
        json.dumps(
            {
                "proxy_routes": proxy_routes,
                "frontend_pages": pages,
                "websocket": {"endpoint": "/api/v1/ws/agent"},
            }
        ),
    )


def test_called_path_normalization_separates_params_from_query_assembly() -> None:
    assert _normalize_called("/api/files/${fileId}") == "/api/files/{param}"
    assert _normalize_called("/api/v1/rag/documents${qs}") == "/api/v1/rag/documents"
    assert _normalize_called("/api/v1/rag/sync/sources${qs ? ") == "/api/v1/rag/sync/sources"
    assert _normalize_called("/api/health") == "/api/health"


def test_page_chain_reaches_backend_through_the_proxy(minimal_repo: Path) -> None:
    _write(minimal_repo, "frontend/tsconfig.json", TSCONFIG)
    _write(minimal_repo, "frontend/src/app/[locale]/files/page.tsx", 'import { upload } from "@/lib/file-api";\n')
    _write(minimal_repo, "frontend/src/lib/file-api.ts", 'await fetch("/api/files/upload", {});\n')
    _write(
        minimal_repo,
        "backend/app/api/routes/v1/__init__.py",
        "v1_router.include_router(files.router, tags=[\"files\"])\n",
    )
    _write(
        minimal_repo,
        "backend/app/api/routes/v1/files.py",
        'router = APIRouter(prefix="/files")\n@router.post("/upload")\nasync def upload(): ...\n',
    )
    _interfaces(
        minimal_repo,
        proxy_routes=[
            {
                "frontend_path": "/api/files/upload",
                "file": "frontend/src/app/api/files/upload/route.ts",
                "backend_targets": [{"path_template": "/api/v1/files/upload"}],
            }
        ],
        pages=[{"route": "/files", "file": "frontend/src/app/[locale]/files/page.tsx"}],
    )

    chains = build_site_chains(Context.discover(minimal_repo))
    assert len(chains) == 1
    chain = chains[0]
    assert chain.proxy_handlers == ["frontend/src/app/api/files/upload/route.ts"]
    assert chain.backend_modules == ["app.api.routes.v1.files"]
    assert chain.unmatched_paths == []


def test_direct_backend_call_is_server_side_not_broken(minimal_repo: Path) -> None:
    _write(minimal_repo, "frontend/tsconfig.json", TSCONFIG)
    _write(
        minimal_repo,
        "frontend/src/app/[locale]/admin/page.tsx",
        'const data = await fetch("/api/v1/admin/stats");\n',
    )
    _write(
        minimal_repo,
        "backend/app/api/routes/v1/__init__.py",
        "v1_router.include_router(admin_stats.router, prefix=\"/admin\", tags=[\"admin\"])\n",
    )
    _write(
        minimal_repo,
        "backend/app/api/routes/v1/admin_stats.py",
        '@router.get("/stats")\nasync def stats(): ...\n',
    )
    _interfaces(
        minimal_repo,
        proxy_routes=[],
        pages=[{"route": "/admin", "file": "frontend/src/app/[locale]/admin/page.tsx"}],
    )

    chain = build_site_chains(Context.discover(minimal_repo))[0]
    assert chain.server_calls == ["/api/v1/admin/stats"]
    assert chain.backend_modules == ["app.api.routes.v1.admin_stats"]
    assert chain.unmatched_paths == []


def test_call_into_the_void_is_an_unmatched_path(minimal_repo: Path) -> None:
    _write(minimal_repo, "frontend/tsconfig.json", TSCONFIG)
    _write(
        minimal_repo,
        "frontend/src/app/[locale]/billing/page.tsx",
        'await fetch("/api/billing/credits");\n',
    )
    _interfaces(
        minimal_repo,
        proxy_routes=[],
        pages=[{"route": "/billing", "file": "frontend/src/app/[locale]/billing/page.tsx"}],
    )

    chain = build_site_chains(Context.discover(minimal_repo))[0]
    assert chain.unmatched_paths == ["/api/billing/credits"]
    assert chain.backend_modules == []


def test_websocket_consumer_is_flagged_as_the_exception_not_broken(minimal_repo: Path) -> None:
    _write(minimal_repo, "frontend/tsconfig.json", TSCONFIG)
    _write(
        minimal_repo,
        "frontend/src/app/[locale]/chat/page.tsx",
        'import { useChat } from "@/hooks/use-chat";\n',
    )
    _write(
        minimal_repo,
        "frontend/src/hooks/use-chat.ts",
        'const url = `${wsBase}/api/v1/ws/agent`;\n',
    )
    _interfaces(
        minimal_repo,
        proxy_routes=[],
        pages=[{"route": "/chat", "file": "frontend/src/app/[locale]/chat/page.tsx"}],
    )

    chain = build_site_chains(Context.discover(minimal_repo))[0]
    assert chain.websocket is True
    assert chain.unmatched_paths == []


def test_real_tree_report_shape_and_known_facts(real_context: Context) -> None:
    report = build_boundaries_report(real_context)
    summary = report["summary"]
    assert summary["pages"] > 30
    assert summary["pages_with_api_chains"] > 15
    assert summary["websocket_exception_pages"] >= 1

    chat = next(page for page in report["pages"] if page["route"] == "/chat")
    assert chat.get("websocket") is True, "the chat page consumes the WS exception"

    rag = next(page for page in report["pages"] if page["route"] == "/rag")
    assert any("rag" in module for module in rag.get("backend_modules", [])), (
        "the RAG page reaches the RAG backend routes"
    )


def test_real_tree_report_is_deterministic(real_context: Context) -> None:
    assert canonical_json(build_boundaries_report(real_context)) == canonical_json(
        build_boundaries_report(real_context)
    )
