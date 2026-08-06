"""The determinism contract.

These are kernel-scope tests. If any of them fail, drift detection is meaningless: every
run would report a diff and real drift would be lost in the noise.
"""

from __future__ import annotations

import json
from pathlib import Path

from repo_governance.io_atomic import (
    canonical_json,
    posix,
    read_json,
    render_bytes,
    write_json_atomic,
    write_text_atomic,
)


def test_canonical_json_sorts_keys_and_ends_with_one_newline() -> None:
    rendered = canonical_json({"b": 1, "a": {"d": 2, "c": 3}})
    assert rendered == '{\n  "a": {\n    "c": 3,\n    "d": 2\n  },\n  "b": 1\n}\n'


def test_canonical_json_is_order_independent() -> None:
    assert canonical_json({"a": 1, "b": 2}) == canonical_json({"b": 2, "a": 1})


def test_written_bytes_are_lf_only(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    write_text_atomic(target, "first\r\nsecond\rthird\n")
    assert target.read_bytes() == b"first\nsecond\nthird\n"


def test_written_bytes_have_no_bom(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    write_json_atomic(target, {"key": "vàlue"})
    raw = target.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert "vàlue" in raw.decode("utf-8")


def test_unchanged_write_reports_no_change_and_leaves_file_alone(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    assert write_json_atomic(target, {"a": 1}) is True
    before = target.stat().st_mtime_ns
    assert write_json_atomic(target, {"a": 1}) is False
    assert target.stat().st_mtime_ns == before


def test_write_leaves_no_temporary_files_behind(tmp_path: Path) -> None:
    write_json_atomic(tmp_path / "out.json", {"a": 1})
    assert sorted(item.name for item in tmp_path.iterdir()) == ["out.json"]


def test_failed_write_does_not_clobber_the_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    write_json_atomic(target, {"a": 1})
    original = target.read_bytes()

    class Unserializable:
        pass

    try:
        write_json_atomic(target, {"a": Unserializable()})
    except TypeError:
        pass
    else:  # pragma: no cover - defensive
        raise AssertionError("expected the render to fail")

    assert target.read_bytes() == original
    assert sorted(item.name for item in tmp_path.iterdir()) == ["out.json"]


def test_paths_are_normalized_to_forward_slashes() -> None:
    assert posix("backend\\app\\core\\config.py") == "backend/app/core/config.py"
    assert posix("backend/app") == "backend/app"


def test_read_json_tolerates_a_bom(tmp_path: Path) -> None:
    target = tmp_path / "bom.json"
    target.write_bytes(b"\xef\xbb\xbf" + json.dumps({"a": 1}).encode("utf-8"))
    assert read_json(target) == {"a": 1}


def test_no_generated_file_contains_a_machine_specific_path(real_context) -> None:
    """Generated output must not embed the checkout location.

    An absolute path makes a committed manifest differ between a developer machine and a CI
    runner, so the drift gate fails on every run for a reason that has nothing to do with
    drift. This is invisible when you only ever run on one platform: the configuration
    manifest carried absolute `process.env` reference paths that were identical on every
    Windows run and different the moment CI rebuilt it on Linux.
    """
    import re

    from repo_governance.pipeline import render_all

    root = str(real_context.repo_root).replace("\\", "/")
    # The drive-letter alternative requires a single letter not preceded by another, so it
    # matches `C:/Users` but not the `p:/` inside `http://`.
    absolute = re.compile(r"(?:(?<![A-Za-z])[A-Za-z]:[\\/]|/home/[a-z]|/Users/[A-Za-z]|/root/)")

    offenders: list[str] = []
    for path, content in render_all(real_context).items():
        if root in content.replace("\\", "/"):
            offenders.append(f"{path}: contains the repository root path")
        elif absolute.search(content):
            match = absolute.search(content)
            start = max(0, match.start() - 40)
            offenders.append(f"{path}: absolute path near {content[start : match.end() + 40]!r}")

    assert offenders == [], "\n".join(offenders)


def test_render_bytes_matches_what_write_produces(tmp_path: Path) -> None:
    text = "line one\r\nline two\n"
    target = tmp_path / "out.txt"
    write_text_atomic(target, text)
    assert target.read_bytes() == render_bytes(text)
