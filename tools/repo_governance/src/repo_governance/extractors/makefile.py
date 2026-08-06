"""Makefile extraction.

The Makefile is the authoritative definition of the canonical Compose stacks — which files
combine, and which profiles are always on. The Compose files themselves cannot say this;
without reading the Makefile, governance would have to guess at the combinations, and the
guess would be a second source of truth for something already written down.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

VARIABLE = re.compile(r"^(?P<name>[A-Z_][A-Z0-9_]*)\s*:?\??=\s*(?P<value>.*)$")
TARGET = re.compile(r"^(?P<name>[a-zA-Z][a-zA-Z0-9_-]*):(?!=)")
COMPOSE_FILE = re.compile(r"-f\s+(?P<path>[\w./-]+)")
PROFILE = re.compile(r"--profile\s+(?P<name>[\w-]+)")


@dataclass(frozen=True)
class ComposeStack:
    id: str
    make_variable: str
    compose_files: tuple[str, ...]
    profiles_always_on: tuple[str, ...]


@dataclass
class MakefileExtraction:
    variables: dict[str, str]
    targets: list[str]
    stacks: list[ComposeStack]
    unknowns: list[str]


#: Make variable to stack identifier. Governance names the stacks; the Makefile owns their
#: contents. A new COMPOSE_* variable that is not mapped here surfaces as an unknown.
STACK_IDS = {
    "COMPOSE_BASE": "base",
    "COMPOSE_DEV": "dev",
    "COMPOSE_FRONTEND": "frontend",
    "COMPOSE_PROD": "prod",
}


def _expand(value: str, variables: dict[str, str], depth: int = 0) -> str:
    """Resolve $(VAR) references so a stack built on another stack resolves fully."""
    if depth > 10:
        return value
    for name, replacement in variables.items():
        value = value.replace(f"$({name})", replacement)
    if "$(" in value and depth < 10:
        return _expand(value, variables, depth + 1)
    return value


def extract_makefile(path: Path) -> MakefileExtraction:
    if not path.is_file():
        return MakefileExtraction({}, [], [], [f"{path} does not exist"])

    variables: dict[str, str] = {}
    targets: list[str] = []
    unknowns: list[str] = []

    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.startswith("\t") or not raw.strip() or raw.lstrip().startswith("#"):
            continue
        variable = VARIABLE.match(raw)
        if variable:
            variables[variable.group("name")] = variable.group("value").strip()
            continue
        target = TARGET.match(raw)
        if target and target.group("name") != ".PHONY":
            targets.append(target.group("name"))

    stacks: list[ComposeStack] = []
    for name, value in sorted(variables.items()):
        if not name.startswith("COMPOSE_"):
            continue
        stack_id = STACK_IDS.get(name)
        if stack_id is None:
            unknowns.append(f"{name} defines a Compose stack that governance does not name")
            continue
        expanded = _expand(value, variables)
        stacks.append(
            ComposeStack(
                id=stack_id,
                make_variable=name,
                compose_files=tuple(COMPOSE_FILE.findall(expanded)),
                profiles_always_on=tuple(sorted(set(PROFILE.findall(expanded)))),
            )
        )

    order = {"base": 0, "dev": 1, "frontend": 2, "prod": 3}
    stacks.sort(key=lambda stack: order.get(stack.id, 99))
    return MakefileExtraction(variables, sorted(set(targets)), stacks, unknowns)
