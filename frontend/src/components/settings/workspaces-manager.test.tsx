import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { WorkspaceRecord } from "@/lib/workspaces-api";

import { WorkspacesManager } from "./workspaces-manager";

const workspaces: WorkspaceRecord[] = [];
const update = vi.fn();

vi.mock("@/hooks", () => ({
  useWorkspaces: () => ({
    workspaces,
    isLoading: false,
    error: null,
    refresh: vi.fn(),
    create: vi.fn(),
    update,
    remove: vi.fn(),
  }),
}));

function workspace(overrides: Partial<WorkspaceRecord> = {}): WorkspaceRecord {
  return {
    id: "00000000-0000-0000-0000-000000000000",
    name: "my-app",
    backend_kind: "remote",
    root: "ws-abc123def456",
    repo_url: "https://github.com/org/app",
    ruleset: "default",
    auto_approve: false,
    created_at: "2026-08-23T00:00:00Z",
    updated_at: null,
    ...overrides,
  };
}

function renderWith(records: WorkspaceRecord[]) {
  workspaces.splice(0, workspaces.length, ...records);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <WorkspacesManager />
    </QueryClientProvider>,
  );
}

describe("WorkspacesManager", () => {
  it("shows an empty state when the user has no workspaces", () => {
    renderWith([]);
    expect(screen.getByText("No workspaces yet")).toBeInTheDocument();
  });

  it("lists a workspace with its ruleset and repository", () => {
    renderWith([workspace()]);
    expect(screen.getByText("my-app")).toBeInTheDocument();
    expect(screen.getByText("default")).toBeInTheDocument();
    expect(screen.getByText("https://github.com/org/app")).toBeInTheDocument();
  });

  it("says so when no repository is attached", () => {
    renderWith([workspace({ repo_url: null })]);
    expect(screen.getByText("No repository attached")).toBeInTheDocument();
  });

  it("badges an auto-approving workspace so the override is visible (ADR-006)", () => {
    renderWith([workspace({ auto_approve: true })]);
    expect(screen.getByText("auto-approve")).toBeInTheDocument();
  });

  it("disables the auto-approve switch on a strict workspace", () => {
    renderWith([workspace({ ruleset: "strict" })]);
    const row = screen.getByRole("listitem");
    expect(within(row).getByRole("switch", { name: /auto-approve/i })).toBeDisabled();
  });

  it("disables the auto-approve switch on a read-only workspace", () => {
    renderWith([workspace({ ruleset: "readonly" })]);
    const row = screen.getByRole("listitem");
    expect(within(row).getByRole("switch", { name: /auto-approve/i })).toBeDisabled();
  });
});
