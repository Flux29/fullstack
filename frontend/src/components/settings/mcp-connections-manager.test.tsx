import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { McpConnectionRecord } from "@/lib/mcp-connections-api";

import { McpConnectionsManager } from "./mcp-connections-manager";

const connections: McpConnectionRecord[] = [];

vi.mock("@/hooks", () => ({
  useMcpConnections: () => ({
    connections,
    isLoading: false,
    error: null,
    refresh: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    test: vi.fn(),
  }),
}));

vi.mock("@/lib/mcp-connections-api", () => ({
  listWorkspaceMcpServers: () => Promise.resolve([]),
  startMcpOAuth: vi.fn(),
}));

/** A connection as the API returns it: `url` is origin-only, because the
 * backend stores no path or query — either can carry the provider's secret. */
function connection(overrides: Partial<McpConnectionRecord>): McpConnectionRecord {
  return {
    id: "00000000-0000-0000-0000-000000000000",
    name: "context7",
    url: "https://mcp.context7.com",
    has_auth_token: false,
    allowed_tools: null,
    is_enabled: true,
    auth_type: "bearer",
    oauth_authorized: false,
    last_status: "ok",
    last_error: null,
    last_checked_at: null,
    created_at: "2026-08-03T00:00:00Z",
    updated_at: null,
    ...overrides,
  };
}

function renderWith(records: McpConnectionRecord[]) {
  connections.splice(0, connections.length, ...records);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <McpConnectionsManager />
    </QueryClientProvider>,
  );
}

/** The catalog card carrying *description*. */
function card(description: RegExp) {
  const element = screen.getByText(description).closest(".rounded-xl");
  if (!element) throw new Error(`No catalog card for ${description}`);
  return within(element as HTMLElement);
}

// Regression: the backend reduced the stored display URL to its origin, but
// the cards still compared it against the path-bearing catalog URL. Every
// fixed-URL card then reported itself unconnected, so its Connect button
// re-POSTed a name the backend already held — an opaque 409 Conflict — and
// every Google card offered an upgrade that was already applied.
describe("McpConnectionsManager catalog cards", () => {
  it("shows a fixed-URL connection stored origin-only as connected", () => {
    renderWith([
      connection({ name: "context7", url: "https://mcp.context7.com" }),
      connection({ id: "hf", name: "huggingface", url: "https://huggingface.co" }),
    ]);

    for (const description of [/Up-to-date documentation/, /Search models, datasets and papers/]) {
      expect(card(description).getByText("Connected")).toBeInTheDocument();
      expect(card(description).queryByRole("button", { name: "Connect" })).toBeNull();
    }
  });

  it("still offers Connect for a catalog entry the user has no connection for", () => {
    renderWith([connection({ name: "context7", url: "https://mcp.context7.com" })]);

    expect(card(/Search models, datasets and papers/).getByRole("button", { name: "Connect" }));
    expect(card(/Search models, datasets and papers/).queryByText("Connected")).toBeNull();
  });

  it("stops offering a standard-API upgrade once the connection is on that host", () => {
    renderWith([
      connection({
        name: "google-docs",
        url: "https://docs.googleapis.com",
        auth_type: "oauth",
        oauth_authorized: true,
      }),
    ]);

    expect(card(/delete documents through the standard Docs API/).getByText("Connected"));
    expect(screen.queryByRole("button", { name: "Upgrade to standard API" })).toBeNull();
  });

  it("still offers the upgrade for a connection left on a superseded host", () => {
    renderWith([
      connection({
        name: "google-docs",
        url: "https://mcp.googleapis.com",
        auth_type: "oauth",
        oauth_authorized: true,
      }),
    ]);

    expect(
      card(/delete documents through the standard Docs API/).getByRole("button", {
        name: "Upgrade to standard API",
      }),
    ).toBeInTheDocument();
  });
});
