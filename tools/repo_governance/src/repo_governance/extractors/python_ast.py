"""Python AST extraction.

Never imports the module it reads. `backend/app/core/config.py` builds a settings singleton
at module scope and `find_env_file` walks parent directories for a real `.env`, so importing
it from tooling would load live credentials. Parsing the file is not a compromise here — it
is the only correct way to read it.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SettingsField:
    name: str
    annotation: str | None
    default: str | None
    has_default: bool


@dataclass
class SettingsExtraction:
    """What could be read from the settings module, and what could not."""

    fields: list[SettingsField] = field(default_factory=list)
    computed: list[str] = field(default_factory=list)
    constants: dict[str, str] = field(default_factory=dict)
    unknowns: list[str] = field(default_factory=list)


def _literal(node: ast.expr | None) -> str | None:
    """Render a default expression as text, or None when it is not a simple value."""
    if node is None:
        return None
    if isinstance(node, ast.Constant):
        if node.value is None:
            return None
        if isinstance(node.value, bool):
            return "true" if node.value else "false"
        return str(node.value)
    if isinstance(node, ast.Call):
        # Field(default=..., ...) and similar wrappers.
        for keyword in node.keywords:
            if keyword.arg in {"default", "default_factory"}:
                return _literal(keyword.value)
        if node.args:
            return _literal(node.args[0])
        return None
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        return None
    return None


def _is_computed(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        name = getattr(target, "id", None) or getattr(target, "attr", None)
        if name in {"computed_field", "property", "cached_property"}:
            return True
    return False


def extract_settings(path: Path, class_name: str = "Settings") -> SettingsExtraction:
    """Read the settings class without importing it."""
    result = SettingsExtraction()

    if not path.is_file():
        result.unknowns.append(f"{path} does not exist")
        return result

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        result.unknowns.append(f"could not parse {path}: {exc}")
        return result

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    value = _literal(node.value)
                    if value is not None:
                        result.constants[target.id] = value

    settings_class = next(
        (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == class_name),
        None,
    )
    if settings_class is None:
        result.unknowns.append(f"class {class_name} not found in {path}")
        return result

    for node in settings_class.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            if not name.isupper():
                continue
            result.fields.append(
                SettingsField(
                    name=name,
                    annotation=ast.unparse(node.annotation) if node.annotation else None,
                    default=_literal(node.value),
                    has_default=node.value is not None,
                )
            )
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and _is_computed(node):
            result.computed.append(node.name)

    result.fields.sort(key=lambda item: item.name)
    result.computed.sort()
    return result


def extract_frozenset_members(path: Path, name: str) -> list[str] | None:
    """Read a module-level frozenset or set of string literals.

    Returns None when the name is absent or is not a literal collection — the caller must
    treat that as unknown rather than as an empty allowlist.
    """
    if not path.is_file():
        return None
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            continue

        value = node.value
        if isinstance(value, ast.Call) and getattr(value.func, "id", None) == "frozenset":
            value = value.args[0] if value.args else None
        if isinstance(value, ast.Set | ast.List | ast.Tuple):
            members = [element.value for element in value.elts if isinstance(element, ast.Constant)]
            if len(members) == len(value.elts):
                return sorted(members)
        return None

    return None


def module_contains(path: Path, needles: tuple[str, ...]) -> bool:
    """Cheap textual presence test, used where an AST match would be over-precise."""
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    return all(needle in text for needle in needles)
