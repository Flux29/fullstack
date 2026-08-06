# CLAUDE.md

**[AGENTS.md](AGENTS.md) is the entry point.** Read it first — it carries the entry rules,
the command surface, the hard boundaries, and where to look for everything else. This file
holds only the deltas that are specific to Claude Code.

## Claude-specific notes

- `.claude/rules/*.md` load automatically when you edit a matching file, so the conventions
  for the file in front of you arrive without being asked for. Do not restate them in code
  comments or commit messages.
- `.claude/skills/` covers recurring tasks: `add-endpoint`, `alembic-migration`,
  `agent-tool`, `background-task`, `frontend-feature`, `pytest-suite`, `rag-knowledge`.
  Prefer a skill over improvising when one fits.
- The Makefile is PowerShell-based and Windows-first. Its preflight and guard targets shell
  out to `powershell.exe`; a few targets are POSIX-flavoured. When a Make target misbehaves
  in a non-Windows shell, run the underlying command rather than rewriting the target.
- Backend commands run through uv from the repository root:
  `uv run --directory backend <command>`. The governance CLI is a separate project:
  `uv run --project tools/repo_governance governance <command>`. Both are wrapped by Make
  targets; use those.
- Do not add project dependencies to a global Python. Every project here has its own
  `pyproject.toml`, `uv.lock`, and `.venv`.

## Done means verified

- Logic has tests. UI has a browser or Playwright check. LLM workflows have pydantic-evals
  or a structured smoke test.
- Final answers state what was validated and what remains uncertain — the governance change
  record has fields for exactly this, and leaving them vague defeats the point.
