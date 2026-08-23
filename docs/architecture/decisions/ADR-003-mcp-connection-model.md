---
id: ADR-003
title: MCP connections are one abstraction with orthogonal dimensions
status: accepted
date: 2026-08-06
components:
  - agents
  - mcp-user-connections
  - github-mcp
---

# ADR-003 — MCP connections are one abstraction with orthogonal dimensions

## Status

Accepted, 2026-08-06. The model is settled; the security posture it exposes is **not**, and
this ADR does not accept that risk. See Open questions.

## Context

It is tempting to model the tool surface as tiers: "container MCPs", "user MCPs", "Google
tools". That framing is wrong here and produces a security model that looks at the wrong
things — it would scrutinize `github-mcp`, which is already locked down at three layers,
and be blind to an arbitrary user-added server with write access.

What actually exists is **one connection abstraction** whose instances vary along
independent axes:

| Axis | Values |
| --- | --- |
| Provisioning | deployment-managed (from the `MCP_SERVERS` setting) · per-user (rows in `mcp_connections`) |
| Executor | generic MCP client · direct Google REST toolsets built from a product data registry |
| Transport | streamable HTTP · SSE, inferred from the URL |
| Authentication | none · bearer · OAuth 2.1 with PKCE and dynamic client registration · GitHub |
| Allowlist source | settings validator · per-connection `allowed_tools` · product registry |
| Approval gating | deferred tools · none |
| Encryption at rest | Fernet · none |

These combine freely. A per-user connection can be OAuth-authenticated with no allowlist and
no approval gate; a deployment-managed one can be bearer-authenticated with a frozen
allowlist and no gate. Tiers cannot express that; coordinates can.

## Decision

**Model connections by their coordinates on these axes, and derive security findings from
the combination rather than from the category.**

Consequences that follow directly:

- **GitHub read-only is a cross-check, not a declaration.** Read-only is enforced at three
  independent layers — container command flags, the settings-validator allowlist frozenset,
  and a runtime post-probe assert. Governance verifies the three agree. Re-declaring the
  policy in a manifest would add a fourth place to drift.
- **Per-user connections are runtime evidence only.** They exist as database rows. Static
  extraction cannot see them, and pretending otherwise would produce a permission map that
  is confidently incomplete. They are enumerated only from a sanitized runtime snapshot,
  never committed.
- **Connection URLs are treated as credential-bearing.** The catalog supports placing a
  token in the URL, and the `url` column is not encrypted while `auth_token` is. Every
  governance surface strips query strings and userinfo before storing or logging a URL.
- **The frontend catalog and the backend URL-to-kind map are a checked contract.** A catalog
  entry that maps to no backend kind means a connection the UI offers and the executor does
  not recognize.

## Open questions — deliberately not resolved here

Three findings are registered as open. This ADR records them; it does **not** accept them.

1. **Approval gating is asymmetric.** Deferred-tool approval covers Google mutation tools
   only. MCP-sourced tools bypass it entirely, and a connection with `allowed_tools` unset
   exposes every tool its server advertises. A write-capable arbitrary server therefore has
   full exposure and no gate. This needs a product-security decision — approval parity,
   default allowlists, or a documented restriction. **An accepted-risk ADR is not sufficient**,
   which is why this section is open questions rather than a decision.
2. **SSRF validation is write-time only.** The URL is validated on create, update, and
   during OAuth flows, but the per-turn probe and toolset attach open the stored URL without
   revalidating — a DNS-rebinding window. Remediation is connection-time revalidation, and
   preferably network-level egress controls.
3. **URL-embedded credentials are stored unencrypted and echoed by the API.**

Until (1) is decided, MCP policies stay advisory. Promoting them to blocking while the gap
is open would encode the current posture as intended.
