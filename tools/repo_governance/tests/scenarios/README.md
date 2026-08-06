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
