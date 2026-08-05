import { describe, expect, it } from "vitest";

import { MCP_CATALOG } from "./mcp-catalog";

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
