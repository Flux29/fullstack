"""The scan/sync/check pipeline.

Everything governance generates is produced by `render_all`, which returns a mapping of
repo-relative path to file content. `sync` writes that mapping; `check` compares it against
what is committed. Because both commands share one producer, a drifted file and a
hand-edited file are the same condition and are reported identically — there is no path by
which check and sync can disagree about what the output should be.
"""

from __future__ import annotations

from repo_governance.config import Context
from repo_governance.io_atomic import canonical_json, render_bytes, write_text_atomic

CATALOG_PATH = "governance/catalog.json"
COMPONENTS_PATH = "governance/manifests/generated/components.json"
EFFECTIVE_PATH = "governance/manifests/effective/repository.json"


def build_catalog(ctx: Context, generated_paths: frozenset[str]) -> dict:
    """Build the navigation index from the catalog specification in `governance.toml`.

    Entries whose path does not exist yet are skipped rather than emitted as broken
    pointers, so the catalog is honest during phased rollout: it lists what an agent can
    actually open today.

    `generated_paths` are the files this same render is about to write. They count as
    present even when they are not on disk yet — otherwise the catalog would omit itself
    and every other generated document on a first run, and the following sync would add
    them, which would make sync non-idempotent from a clean checkout.
    """
    entries = []
    for spec in ctx.config.catalog_spec:
        path = spec["path"]
        target = ctx.repo_root / path.rstrip("/")
        if path in generated_paths:
            exists = True
        else:
            exists = target.is_dir() if path.endswith("/") else target.is_file()
        if not exists:
            continue
        entries.append(
            {
                "path": path,
                "kind": spec["kind"],
                "description": spec["description"],
                "authority": spec["authority"],
                "update": spec["update"],
                "read_when": spec["read_when"],
            }
        )

    entries.sort(key=lambda entry: entry["path"])
    return {
        "schema_version": ctx.config.schema_version,
        "provenance": {
            "method": "extracted",
            "sources": ["governance/governance.toml"],
            "extractor_version": ctx.config.version,
        },
        "entries": entries,
    }


def render_all(ctx: Context) -> dict[str, str]:
    """Produce every governance-generated file as `{repo-relative path: content}`.

    Later phases extend this mapping; the contract stays the same. Nothing here may read
    the clock, the hostname, an absolute path, or an environment variable — a generated
    file that varies between machines makes drift detection meaningless.
    """
    outputs: dict[str, str] = {}

    from repo_governance.merge import build_components, build_effective

    components, _ = build_components(ctx)
    outputs[COMPONENTS_PATH] = canonical_json(components)
    outputs[EFFECTIVE_PATH] = canonical_json(build_effective(ctx))

    # The catalog is rendered last because it describes the whole output set, including
    # itself. Everything else is added above this line.
    generated_paths = frozenset({*outputs, CATALOG_PATH})
    outputs[CATALOG_PATH] = canonical_json(build_catalog(ctx, generated_paths))
    return outputs


def sync(ctx: Context) -> list[str]:
    """Write the rendered outputs. Returns the paths whose bytes actually changed."""
    changed: list[str] = []
    for relative, content in sorted(render_all(ctx).items()):
        if write_text_atomic(ctx.repo_root / relative, content):
            changed.append(relative)
    return changed


def compare_generated(ctx: Context) -> list[tuple[str, str]]:
    """Compare rendered output against what is on disk.

    Returns `(path, reason)` for every file that does not match. A missing file and a
    hand-edited file are both reported: in either case the committed bytes no longer follow
    from the sources.
    """
    mismatches: list[tuple[str, str]] = []
    for relative, content in sorted(render_all(ctx).items()):
        target = ctx.repo_root / relative
        expected = render_bytes(content)
        if not target.is_file():
            mismatches.append((relative, "generated file is missing"))
            continue
        actual = target.read_bytes()
        if actual != expected:
            reason = "content differs from what the sources produce"
            if actual.replace(b"\r\n", b"\n") == expected:
                reason = "line endings are CRLF; governance output must be LF"
            mismatches.append((relative, reason))
    return mismatches
