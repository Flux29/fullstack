import { describe, expect, it } from "vitest";

import {
  catalogOrigin,
  catalogUpgradeAvailable,
  findCatalogConnection,
  MCP_CATALOG,
  type McpCatalogEntry,
} from "./mcp-catalog";

function entryFor(id: string): McpCatalogEntry {
  const entry = MCP_CATALOG.find((candidate) => candidate.id === id);
  if (!entry) throw new Error(`No catalog entry ${id}`);
  return entry;
}

/** A connection as the API returns it: `url` is origin-only (the backend
 * stores no path or query, since either can carry the provider's secret). */
function stored(name: string, url: string) {
  return { name, url };
}

describe("Google Workspace standard API catalog", () => {
  it("uses generally available roots for all converted products", () => {
    const expected = {
      "google-drive": "https://www.googleapis.com/drive/v3",
      "google-docs": "https://docs.googleapis.com/v1",
      "google-sheets": "https://sheets.googleapis.com/v4",
      "google-slides": "https://slides.googleapis.com/v1",
      "google-chat": "https://chat.googleapis.com/v1",
      "google-contacts": "https://people.googleapis.com/v1",
    };

    for (const [id, url] of Object.entries(expected)) {
      const entry = MCP_CATALOG.find((candidate) => candidate.id === id);
      expect(entry?.url).toBe(url);
      expect(entry?.allowedTools?.length).toBeGreaterThan(5);
      expect(entry?.url).not.toContain("mcp.googleapis.com");
    }
  });

  it("offers approval-gated Gmail send operations for selection", () => {
    const gmail = MCP_CATALOG.find((candidate) => candidate.id === "gmail");

    expect(gmail?.allowedTools).toEqual(
      expect.arrayContaining(["create_draft", "send_draft", "send_message"]),
    );
  });
});

describe("catalogOrigin", () => {
  it("reduces a catalog URL to the origin the backend stores", () => {
    expect(catalogOrigin("https://mcp.context7.com/mcp")).toBe("https://mcp.context7.com");
    expect(catalogOrigin("https://huggingface.co/mcp")).toBe("https://huggingface.co");
    expect(catalogOrigin("https://api.githubcopilot.com/mcp/")).toBe(
      "https://api.githubcopilot.com",
    );
    expect(catalogOrigin("https://docs.googleapis.com/v1")).toBe("https://docs.googleapis.com");
  });

  it("drops a token carried in the query string", () => {
    expect(catalogOrigin("https://mcp.alphavantage.co/mcp?apikey=s3cret")).toBe(
      "https://mcp.alphavantage.co",
    );
  });
});

describe("findCatalogConnection", () => {
  // Regression: the backend reduced the stored display URL to its origin
  // while the UI still compared it against the path-bearing catalog URL, so
  // every fixed-URL card reported itself unconnected and its Connect button
  // re-POSTed a name the backend already held — an opaque 409 Conflict.
  it("recognizes a fixed-URL connection stored with an origin-only URL", () => {
    const connections = [
      stored("context7", "https://mcp.context7.com"),
      stored("huggingface", "https://huggingface.co"),
    ];

    expect(findCatalogConnection(connections, entryFor("context7"))?.name).toBe("context7");
    expect(findCatalogConnection(connections, entryFor("huggingface"))?.name).toBe("huggingface");
    expect(findCatalogConnection(connections, entryFor("exa"))).toBeNull();
  });

  it("matches a hand-added connection to its catalog entry by origin", () => {
    const connections = [stored("my-docs-server", "https://mcp.context7.com")];

    expect(findCatalogConnection(connections, entryFor("context7"))?.name).toBe("my-docs-server");
  });

  it("matches OAuth entries by name only, so products sharing a host stay distinct", () => {
    // google-drive and google-calendar are both www.googleapis.com.
    const connections = [stored("google-calendar", "https://www.googleapis.com")];

    expect(findCatalogConnection(connections, entryFor("google-calendar"))?.name).toBe(
      "google-calendar",
    );
    expect(findCatalogConnection(connections, entryFor("google-drive"))).toBeNull();
  });

  it("matches a personal-link entry by name, having no catalog URL to compare", () => {
    const connections = [stored("zapier", "https://mcp.zapier.com")];

    expect(findCatalogConnection(connections, entryFor("zapier"))?.name).toBe("zapier");
  });
});

describe("catalogUpgradeAvailable", () => {
  it("leaves a connection on the current catalog host alone", () => {
    expect(catalogUpgradeAvailable(stored("google-docs", "https://docs.googleapis.com"))).toBe(
      false,
    );
  });

  it("flags an OAuth connection still on a superseded host", () => {
    expect(catalogUpgradeAvailable(stored("google-docs", "https://mcp.googleapis.com"))).toBe(true);
  });

  it("never flags a connection the catalog does not list", () => {
    expect(catalogUpgradeAvailable(stored("my-own-server", "https://mcp.example.com"))).toBe(false);
    expect(catalogUpgradeAvailable(stored("context7", "https://mcp.context7.com"))).toBe(false);
  });
});
