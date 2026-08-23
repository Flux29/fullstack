"use client";

import { useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { qk } from "@/lib/query-keys";
import { getErrorMessage } from "@/lib/utils";
import {
  createWorkspace,
  deleteWorkspace,
  listWorkspaces,
  updateWorkspace,
  type WorkspaceCreateInput,
  type WorkspaceRecord,
  type WorkspaceUpdateInput,
} from "@/lib/workspaces-api";

interface UseWorkspacesResult {
  workspaces: WorkspaceRecord[];
  isLoading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  create: (input: WorkspaceCreateInput) => Promise<WorkspaceRecord>;
  update: (id: string, patch: WorkspaceUpdateInput) => Promise<WorkspaceRecord>;
  remove: (id: string) => Promise<void>;
}

/**
 * Manages the user's coding workspaces (Settings → Workspaces).
 *
 * Same shape as `useMcpConnections`: React Query owns the list, mutations
 * patch the cache in place, and errors propagate as throws for toasts.
 */
export function useWorkspaces(): UseWorkspacesResult {
  const queryClient = useQueryClient();

  const {
    data: workspaces = [],
    isLoading,
    error: queryError,
    refetch,
  } = useQuery({
    queryKey: qk.workspaces.list(),
    queryFn: listWorkspaces,
  });

  const error = queryError ? getErrorMessage(queryError, "Failed to load workspaces") : null;

  const writeCache = useCallback(
    (updater: (prev: WorkspaceRecord[]) => WorkspaceRecord[]) =>
      queryClient.setQueryData<WorkspaceRecord[]>(qk.workspaces.list(), (prev = []) =>
        updater(prev),
      ),
    [queryClient],
  );

  const refresh = useCallback(async () => {
    await refetch();
  }, [refetch]);

  const create = useCallback<UseWorkspacesResult["create"]>(
    async (input) => {
      const created = await createWorkspace(input);
      writeCache((prev) => [...prev, created]);
      return created;
    },
    [writeCache],
  );

  const update = useCallback<UseWorkspacesResult["update"]>(
    async (id, patch) => {
      const updated = await updateWorkspace(id, patch);
      writeCache((prev) => prev.map((w) => (w.id === id ? updated : w)));
      return updated;
    },
    [writeCache],
  );

  const remove = useCallback<UseWorkspacesResult["remove"]>(
    async (id) => {
      await deleteWorkspace(id);
      writeCache((prev) => prev.filter((w) => w.id !== id));
    },
    [writeCache],
  );

  return { workspaces, isLoading, error, refresh, create, update, remove };
}
