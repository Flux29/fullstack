"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * Per-client chat-mode toggle: whether the next turn runs as a deep-research
 * turn (planner + parallel subagents + cited report) or as a normal chat turn.
 * Carried on the WS payload as `deep_research`. Persisted so the preferred mode
 * survives a refresh; the backend forces normal chat when the feature is off.
 *
 * `workspaceId` names the coding workspace the next turn may act on (ADR-006),
 * carried as `workspace_id`. The backend attaches nothing when coding is off or
 * the workspace is not the caller's, so a stale persisted id is harmless.
 *
 * `customModel` is the latest model name the user typed into the controls panel
 * rather than picked from the server list — kept so an out-of-date
 * AI_AVAILABLE_MODELS never forces a repo edit to use a newer model. The
 * backend forwards any model string unvalidated, so a bad name fails per-turn.
 */
interface ChatModeState {
  deepResearch: boolean;
  setDeepResearch: (on: boolean) => void;
  toggleDeepResearch: () => void;
  workspaceId: string | null;
  setWorkspaceId: (id: string | null) => void;
  customModel: string | null;
  setCustomModel: (name: string | null) => void;
}

export const useChatModeStore = create<ChatModeState>()(
  persist(
    (set) => ({
      deepResearch: false,
      setDeepResearch: (on) => set({ deepResearch: on }),
      toggleDeepResearch: () => set((s) => ({ deepResearch: !s.deepResearch })),
      workspaceId: null,
      setWorkspaceId: (id) => set({ workspaceId: id }),
      customModel: null,
      setCustomModel: (name) => set({ customModel: name }),
    }),
    {
      name: "chat-mode",
      version: 2,
    },
  ),
);
