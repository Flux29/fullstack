import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Decision } from "@/types";

interface CapturedWsOptions {
  url: string;
  protocols?: string[];
  onMessage?: (event: MessageEvent) => void;
  onOpen?: () => void;
  onClose?: (event: CloseEvent) => void;
}

const h = vi.hoisted(() => ({
  connect: vi.fn(),
  disconnect: vi.fn(),
  sendMessage: vi.fn(),
  isConnected: true,
  lastOptions: null as unknown,
}));

vi.mock("./use-websocket", () => ({
  useWebSocket: (opts: unknown) => {
    h.lastOptions = opts;
    return {
      isConnected: h.isConnected,
      connect: h.connect,
      disconnect: h.disconnect,
      sendMessage: h.sendMessage,
    };
  },
}));

import { useChat } from "./use-chat";
import { useAuthStore, useChatStore, useResearchStore } from "@/stores";

const wsOptions = () => h.lastOptions as CapturedWsOptions;

const serverEvent = (type: string, data: Record<string, unknown>) =>
  ({ data: JSON.stringify({ type, data }) }) as MessageEvent;

const approvalRequired = () =>
  serverEvent("tool_approval_required", {
    action_requests: [{ id: "call-1", tool_name: "execute", args: { command: "ls" } }],
    review_configs: [{ tool_name: "execute", allow_edit: true }],
  });

describe("useChat token-refresh socket lifecycle", () => {
  beforeEach(() => {
    h.connect.mockClear();
    h.disconnect.mockClear();
    h.sendMessage.mockClear();
    h.isConnected = true;
    useChatStore.getState().clearMessages();
    useResearchStore.getState().resetAll();
    act(() => useAuthStore.getState().setAccessToken(null));
  });

  it("holds the connected token while a turn is in flight and applies the refreshed one after it settles", () => {
    act(() => useAuthStore.getState().setAccessToken("token-1"));
    const { result } = renderHook(() => useChat());
    expect(wsOptions().protocols).toEqual(["access_token.token-1", "chat"]);

    // Start a turn and park it on an approval gate.
    act(() => result.current.sendMessage("run the build"));
    act(() => wsOptions().onMessage?.(approvalRequired()));
    expect(result.current.pendingApproval).not.toBeNull();

    // The scheduled refresh rotates the in-memory token mid-turn: the socket
    // must keep the token it authenticated with, or the reconnect kills the
    // turn and orphans the approval.
    act(() => useAuthStore.getState().setAccessToken("token-2"));
    expect(wsOptions().protocols).toEqual(["access_token.token-1", "chat"]);

    // Approve and finish the turn — now the refreshed token may be applied.
    act(() => result.current.sendResumeDecisions([{ id: "call-1", type: "approve" } as Decision]));
    act(() => wsOptions().onMessage?.(serverEvent("complete", {})));
    expect(wsOptions().protocols).toEqual(["access_token.token-2", "chat"]);
  });

  it("applies a refreshed token immediately when the socket dropped mid-turn", () => {
    act(() => useAuthStore.getState().setAccessToken("token-1"));
    const { result, rerender } = renderHook(() => useChat());
    act(() => result.current.sendMessage("long running turn"));
    expect(result.current.isProcessing).toBe(true);

    // Socket drops while the turn runs: the server-side turn is dead anyway,
    // and recovery needs the fresh token on the reconnecting socket.
    h.isConnected = false;
    rerender();
    act(() => useAuthStore.getState().setAccessToken("token-2"));
    expect(wsOptions().protocols).toEqual(["access_token.token-2", "chat"]);
  });

  it("reconciles a stuck approval when the server answers turn_not_active", () => {
    act(() => useAuthStore.getState().setAccessToken("token-1"));
    const { result } = renderHook(() => useChat());
    act(() => result.current.sendMessage("run the build"));
    act(() => wsOptions().onMessage?.(approvalRequired()));
    expect(result.current.isProcessing).toBe(true);

    act(() => wsOptions().onMessage?.(serverEvent("turn_not_active", { frame_type: "resume" })));
    expect(result.current.pendingApproval).toBeNull();
    expect(result.current.isProcessing).toBe(false);
  });

  it("tears the socket down when the access token is cleared on logout", () => {
    act(() => useAuthStore.getState().setAccessToken("token-1"));
    renderHook(() => useChat());
    expect(wsOptions().protocols).toEqual(["access_token.token-1", "chat"]);

    h.disconnect.mockClear();
    act(() => useAuthStore.getState().setAccessToken(null));
    expect(h.disconnect).toHaveBeenCalled();
  });

  it("reconciles in-flight turn state left over from a previous socket on reconnect", () => {
    act(() => useAuthStore.getState().setAccessToken("token-1"));
    const { result } = renderHook(() => useChat());
    act(() => result.current.sendMessage("long running turn"));
    expect(result.current.isProcessing).toBe(true);

    // A fresh socket owns no server-side turn; onOpen must clear the orphaned
    // in-flight state so the input is usable again.
    act(() => wsOptions().onOpen?.());
    expect(result.current.isProcessing).toBe(false);
    expect(result.current.pendingApproval).toBeNull();
  });
});
