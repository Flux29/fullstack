"""OpenAPI extraction through a scrubbed subprocess.

FastAPI's route table cannot be read reliably from an AST — routers are composed, prefixes
are applied at include time, and dependencies rewrite signatures. So this is the one place
governance executes application code, and it does so in a subprocess that cannot reach the
repository's `.env` or the telemetry token.

If the export fails for any reason, the result is `status: "unknown"` with the reason
attached. It is never an empty path list: "the exporter could not run" and "the application
has no routes" are very different claims.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from repo_governance.config import Context
from repo_governance.io_atomic import read_json

#: Environment variables the subprocess is allowed to inherit. Everything else — including
#: LOGFIRE_TOKEN and every credential — is dropped.
ENV_ALLOWLIST = (
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
    "LANG",
    "LC_ALL",
    "UV_CACHE_DIR",
    "UV_PYTHON",
    "VIRTUAL_ENV",
)

TIMEOUT_SECONDS = 180


@dataclass
class OpenApiExtraction:
    status: str = "unknown"
    unknown_reason: str | None = None
    paths: list[dict[str, str]] = field(default_factory=list)

    @property
    def route_count(self) -> int:
        return len(self.paths)


def _scrubbed_environment() -> dict[str, str]:
    environment = {name: os.environ[name] for name in ENV_ALLOWLIST if name in os.environ}
    # Make the application's own environment checks resolve to the safest branch.
    environment["ENVIRONMENT"] = "local"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment.pop("LOGFIRE_TOKEN", None)
    return environment


def extract_openapi(ctx: Context) -> OpenApiExtraction:
    backend = ctx.repo_root / "backend"
    script = Path(__file__).with_name("openapi_export_script.py")

    if not backend.is_dir():
        return OpenApiExtraction(unknown_reason="backend/ does not exist")

    with tempfile.TemporaryDirectory(prefix="governance-openapi-") as workdir:
        output = Path(workdir) / "openapi.json"
        # Copy the exporter next to its output rather than running it in place. Python puts
        # the script's own directory at the front of sys.path, and this package contains an
        # `mcp.py`, which would shadow the real `mcp` package the backend imports. Running
        # from a directory containing nothing but the script removes the whole class of
        # shadowing bug.
        runner = Path(workdir) / "export_openapi.py"
        shutil.copyfile(script, runner)
        try:
            completed = subprocess.run(
                [
                    "uv",
                    "run",
                    "--project",
                    str(backend),
                    "python",
                    str(runner),
                    str(output),
                ],
                # Outside the repository, so find_env_file's cwd-and-parent walk cannot
                # reach backend/.env.
                cwd=workdir,
                env=_scrubbed_environment(),
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
        except FileNotFoundError:
            return OpenApiExtraction(unknown_reason="uv is not on PATH")
        except subprocess.TimeoutExpired:
            return OpenApiExtraction(unknown_reason=f"the exporter did not finish within {TIMEOUT_SECONDS}s")

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip().splitlines()
            tail = detail[-1] if detail else "no output"
            return OpenApiExtraction(unknown_reason=f"the exporter exited {completed.returncode}: {tail}")

        if not output.is_file():
            return OpenApiExtraction(unknown_reason="the exporter reported success but wrote no file")

        try:
            schema = read_json(output)
        except ValueError as exc:
            return OpenApiExtraction(unknown_reason=f"the exported schema is not valid JSON: {exc}")

    paths = [
        {"method": method.upper(), "path": path}
        for path, operations in sorted((schema.get("paths") or {}).items())
        for method in sorted(operations)
        if method.lower() in {"get", "post", "put", "patch", "delete", "head", "options"}
    ]
    return OpenApiExtraction(status="extracted", paths=paths)


def python_executable() -> str:  # pragma: no cover - diagnostic helper
    return sys.executable
