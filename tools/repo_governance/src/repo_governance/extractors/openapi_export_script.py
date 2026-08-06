# governance:runs-in-scrubbed-subprocess
"""Dump the FastAPI OpenAPI schema to a file.

This is the **only** governance code that imports the application, and it never runs in the
governance process. It is executed by `openapi.py` in a subprocess whose working directory
is outside the repository and whose environment has been reduced to a small allowlist.

Both precautions are load-bearing. `app.core.config.find_env_file()` looks for a `.env` in
the current directory and its parent, so a working directory inside the repository would
load real credentials. `app.main` configures Logfire at module import scope with
`send_to_logfire="if-token-present"`, so an inherited `LOGFIRE_TOKEN` would emit telemetry
from what is supposed to be a read-only inspection.

Run as: python openapi_export_script.py <output.json>
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: openapi_export_script.py <output.json>", file=sys.stderr)
        return 2

    from app.main import app

    schema = app.openapi()
    with open(sys.argv[1], "w", encoding="utf-8", newline="\n") as handle:
        json.dump(schema, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
