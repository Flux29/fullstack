"""Internal typed models.

These mirror the committed JSON Schemas for the CLI's own convenience. The schemas under
`governance/schemas/` remain the validation authority — when the two disagree, the schema
is right and this module is stale. Nothing here is written to disk directly; everything
generated goes out through `io_atomic`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Maturity = Literal["experimental", "advisory", "warning", "blocking", "deprecated", "retired"]
Severity = Literal["low", "medium", "high"]
FindingStatus = Literal["open", "resolved", "accepted"]
ProvenanceMethod = Literal["extracted", "reviewed-baseline", "curated", "merged"]
CatalogAuthority = Literal["generated", "curated", "mixed"]
CatalogUpdate = Literal["sync", "manual", "cli-change"]


class BaseGovernanceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Provenance(BaseGovernanceModel):
    """How a manifest section came to exist.

    `reviewed-baseline` sections are hand-authored and exempt from the regeneration
    comparison; `extracted` sections must reproduce byte-identically from their sources.
    """

    method: ProvenanceMethod
    sources: list[str] = Field(default_factory=list)
    extractor_version: str


class CatalogEntry(BaseGovernanceModel):
    path: str
    kind: str
    description: str
    authority: CatalogAuthority
    update: CatalogUpdate
    read_when: str


class Issue(BaseGovernanceModel):
    """What a check function reports.

    Deliberately carries no rule or maturity: those come from the policy file that declares
    the rule, so a check cannot decide its own severity. `run_checks` pairs each issue with
    its rule to produce a `Finding`.
    """

    message: str
    path: str | None = None
    evidence: str | None = None
    repair: str | None = None


class Finding(BaseGovernanceModel):
    """A single check result.

    Message fields are deliberately separated so output stays actionable in the terms the
    blueprint requires: which source, which rule, what evidence, and how to repair it.
    """

    rule: str
    maturity: Maturity
    message: str
    path: str | None = None
    evidence: str | None = None
    repair: str | None = None

    @property
    def blocking(self) -> bool:
        return self.maturity == "blocking"

    def render(self) -> str:
        location = f"{self.path}: " if self.path else ""
        parts = [f"[{self.maturity}] {location}{self.message} ({self.rule})"]
        if self.evidence:
            parts.append(f"    evidence: {self.evidence}")
        if self.repair:
            parts.append(f"    repair:   {self.repair}")
        return "\n".join(parts)


class CheckReport(BaseModel):
    """The outcome of a check run."""

    model_config = ConfigDict(extra="forbid")

    findings: list[Finding] = Field(default_factory=list)
    checks_run: list[str] = Field(default_factory=list)
    checks_deferred: list[str] = Field(default_factory=list)

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    def extend(self, findings: list[Finding]) -> None:
        self.findings.extend(findings)

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.blocking]

    @property
    def advisory(self) -> list[Finding]:
        return [f for f in self.findings if not f.blocking]

    @property
    def exit_code(self) -> int:
        return 1 if self.blocking else 0
