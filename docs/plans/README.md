# Delivery plans

Phase-by-phase execution plans for the `phased-delivery` workflow, named
`NNN-<topic>.md` and shaped like the template in
`.claude/skills/phased-delivery/references/plan-template.md`. The gate script
parses the `## Phase N` headings and the `Status:` line, so both are
load-bearing; `make governance-sync` reads them the same way to list each plan
under Deliveries in `governance/Summary.md`, so a Status change is committed
together with a sync. Workflow: the `phased-delivery` skill
(`.claude/skills/phased-delivery/`).
