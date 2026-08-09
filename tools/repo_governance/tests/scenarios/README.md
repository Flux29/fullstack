# Scenarios

Small reproducible representations of real changes, each declaring the components,
policies, validators, context documents, and findings it should produce:

- rename an environment variable;
- add a Compose service, and a profile;
- alter the embedding dimension;
- add or remove a FastAPI route, and its proxy route handler;
- change a Next.js API consumer;
- add an Alembic migration, including a branch and a merge revision;
- expand an MCP tool allowlist;
- add a per-user MCP connection (the runtime-evidence path);
- introduce a dependency cycle;
- move a governed subsystem;
- change a governance schema or ranking weight.

These are how a change to extraction, policy, or ranking is judged: replay the scenarios
and compare precision, recall, context size, and runtime against the current behaviour.
Historical change records can seed new ones.

Populated from Phase 3 onward; replay under shadow mode arrives with Phase 9.

## File format

One JSON file per scenario, validated by `SCENARIO_SCHEMA` in `../scenario_contract.py`
(test-side deliberately: scenarios judge the governance system, they are not governed
documents, and the committed schema set stays at twelve). Each file declares:

- `id`, `kind` (`historical` seeded from a change record named in `source_change_record`,
  or `synthetic` from the blueprint list above), `title`, `description`, `notes`;
- `change.paths` — the repo-relative paths the change touches. Synthetic paths need not
  exist on disk; impact analysis matches ownership globs against strings;
- `expected` — the ground-truth `components`, `validators`, `proxy_routes`,
  `context_documents`, `policies`, and `findings` the change *should* select. Expected is
  a judgment, written once; what impact *actually* answers is captured separately.

## Baselines and replay

`../golden/impact-baselines/<id>.before.json` holds the manifest-only impact answer,
captured before the import graph existed; `<id>.after.json` holds the graph-aware answer.
The replay test compares against `.after.json` when present, so both sides of the
before/after comparison stay committed. To recapture after a deliberate behaviour change:

    GOVERNANCE_UPDATE_GOLDENS=1 make governance-selftest

then rerun without the variable to prove determinism. Golden churn when backend imports
change is the desired signal, not noise — the diff is the argument. Per-scenario
precision/recall and context size land in `.cache/repo-governance/scenario-report.json`
on every suite run; runtime and other volatile values never enter a committed golden.
