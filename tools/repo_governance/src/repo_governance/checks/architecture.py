"""Component boundary and dependency-declaration checks.

These operate on declarations, not on the import graph. Whether a declared boundary is
actually respected in code is a Phase 6 question; whether the declaration itself is coherent
is answerable now, and an incoherent declaration makes the Phase 6 answer meaningless.
"""

from __future__ import annotations

from repo_governance.checks import CheckScope
from repo_governance.merge import build_components
from repo_governance.models import Issue


def check_single_declaration(scope: CheckScope) -> list[Issue]:
    """No component is declared both as an annotation and in the curated manifest."""
    _, conflicts = build_components(scope.ctx)
    return [
        Issue(
            message=f"Component {conflict['component']!r} is declared more than once.",
            path="governance/manifests/curated/architectural-intent.json",
            evidence=conflict["detail"],
            repair="Delete one of the declarations. A directory annotation wins when the component has a home directory.",
        )
        for conflict in conflicts
        if conflict["field"] == "declared_in"
    ]


def check_dependency_declarations(scope: CheckScope) -> list[Issue]:
    """Dependency lists name declared components and do not contradict themselves."""
    manifest, _ = build_components(scope.ctx)
    components = manifest["components"]
    known = {component["id"] for component in components}

    issues: list[Issue] = []
    for component in components:
        allowed = set(component.get("allowed_dependencies", []))
        forbidden = set(component.get("forbidden_dependencies", []))
        where = component.get("annotation_path", "governance/manifests/curated/architectural-intent.json")

        for dependency in sorted(allowed | forbidden):
            if dependency not in known:
                issues.append(
                    Issue(
                        message=f"{component['id']!r} names an undeclared component {dependency!r} as a dependency.",
                        path=where,
                        evidence="A dependency rule naming a component that does not exist reads as protection while enforcing nothing.",
                        repair="Declare the component, or correct the name.",
                    )
                )

        for dependency in sorted(allowed & forbidden):
            issues.append(
                Issue(
                    message=f"{component['id']!r} lists {dependency!r} as both allowed and forbidden.",
                    path=where,
                    evidence="The two lists contradict each other, so neither can be enforced.",
                    repair="Remove it from one list.",
                )
            )

    return issues


def check_healthchecks(scope: CheckScope) -> list[Issue]:
    """Services declare a healthcheck, or an active exception names them."""
    from repo_governance.builders import build_services
    from repo_governance.io_atomic import read_json

    manifest = build_services(scope.ctx)
    exceptions_path = scope.ctx.paths.curated_manifests / "exceptions.json"
    excused = ""
    if exceptions_path.is_file():
        excused = " ".join(
            " ".join(entry.get("scope", []))
            for entry in read_json(exceptions_path).get("exceptions", [])
            if entry.get("status") == "active"
        )

    issues: list[Issue] = []
    for service in manifest["services"]:
        if service["healthcheck"]["present"]:
            continue
        if service["name"] in excused:
            continue
        issues.append(
            Issue(
                message=f"Service {service['name']} has no healthcheck and no exception explaining why.",
                path="docker-compose.yml",
                evidence="Compose dependency ordering is only meaningful when readiness is observable.",
                repair="Add a healthcheck, or record an exception naming this service and the reason.",
            )
        )
    return issues


def check_proxy_coverage(scope: CheckScope) -> list[Issue]:
    """Every proxy route handler resolves to a backend target."""
    from repo_governance.extractors.typescript_ast import extract_frontend

    extraction = extract_frontend(scope.ctx)
    issues: list[Issue] = []

    for route in extraction.proxy_routes:
        if route.mechanism != "unknown":
            continue
        issues.append(
            Issue(
                message=f"Proxy handler {route.frontend_path} has no resolvable backend target.",
                path=route.file,
                evidence="Recorded as unknown rather than as having none: impact analysis cannot follow this hop.",
                repair="Use backendFetch with a literal path, or record why this handler has no backend call.",
            )
        )

    if not extraction.proxy_routes:
        issues.append(
            Issue(
                message="No proxy route handlers were found at all.",
                path="frontend/src/app/api",
                evidence="Every REST call from the browser is expected to go through this layer.",
                repair="Check whether the extractor's assumptions about the App Router layout still hold.",
            )
        )

    return issues
