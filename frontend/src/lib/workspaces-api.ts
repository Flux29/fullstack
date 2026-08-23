/**
 * API client for user-owned coding workspaces (Settings → Workspaces; ADR-006).
 *
 * A workspace is the sandboxed filesystem a chat turn can name. The row holds
 * identity and policy only — `root` is the opaque sandbox session id the
 * backend generates; it is never a path and never user-settable.
 */

import { apiClient } from "./api-client";

export type WorkspaceBackendKind = "remote" | "docker";
export type WorkspaceRuleset = "readonly" | "default" | "strict";

export interface WorkspaceRecord {
  id: string;
  name: string;
  backend_kind: WorkspaceBackendKind;
  /** Opaque sandbox session id, generated server-side. */
  root: string;
  /** Stored without userinfo, query string, or fragment. */
  repo_url: string | null;
  ruleset: WorkspaceRuleset;
  /** Resolves write/execute approvals without a browser round-trip. Never with `strict`. */
  auto_approve: boolean;
  created_at: string;
  updated_at: string | null;
}

interface WorkspaceList {
  items: WorkspaceRecord[];
  total: number;
}

export interface WorkspaceCreateInput {
  name: string;
  backend_kind?: WorkspaceBackendKind;
  repo_url?: string | null;
  ruleset?: WorkspaceRuleset;
  auto_approve?: boolean;
}

export interface WorkspaceUpdateInput {
  name?: string;
  backend_kind?: WorkspaceBackendKind;
  /** Explicit `null` detaches the repository; omit the key to leave it alone. */
  repo_url?: string | null;
  ruleset?: WorkspaceRuleset;
  auto_approve?: boolean;
}

const ROOT = "/me/workspaces";

export async function listWorkspaces(): Promise<WorkspaceRecord[]> {
  const data = await apiClient.get<WorkspaceList>(ROOT);
  return data.items;
}

export async function createWorkspace(input: WorkspaceCreateInput): Promise<WorkspaceRecord> {
  return apiClient.post<WorkspaceRecord>(ROOT, input);
}

export async function updateWorkspace(
  id: string,
  patch: WorkspaceUpdateInput,
): Promise<WorkspaceRecord> {
  return apiClient.patch<WorkspaceRecord>(`${ROOT}/${id}`, patch);
}

export async function deleteWorkspace(id: string): Promise<void> {
  await apiClient.delete<void>(`${ROOT}/${id}`);
}
