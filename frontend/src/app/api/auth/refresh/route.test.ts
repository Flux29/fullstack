import { describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("@/lib/server-api", () => ({
  backendFetch: vi.fn().mockResolvedValue({
    access_token: "new-access",
    refresh_token: "new-refresh",
    token_type: "bearer",
  }),
  BackendApiError: class BackendApiError extends Error {},
}));

import { POST } from "./route";

function makeRequest(headers: Record<string, string>, withCookie = true): NextRequest {
  return new NextRequest("http://localhost/api/auth/refresh", {
    method: "POST",
    headers: {
      ...(withCookie ? { cookie: "refresh_token=abc" } : {}),
      ...headers,
    },
  });
}

describe("POST /api/auth/refresh", () => {
  it("rejects requests without the X-Token-Refresh marker", async () => {
    const response = await POST(makeRequest({ "sec-fetch-site": "same-origin" }));
    expect(response.status).toBe(403);
  });

  it("rejects cross-site fetch metadata (sandboxed/opaque-origin callers)", async () => {
    const response = await POST(
      makeRequest({ "sec-fetch-site": "cross-site", "x-token-refresh": "1" }),
    );
    expect(response.status).toBe(403);
  });

  it("accepts the app's own same-origin refresh call", async () => {
    const response = await POST(
      makeRequest({ "sec-fetch-site": "same-origin", "x-token-refresh": "1" }),
    );
    expect(response.status).toBe(200);
    const data = (await response.json()) as { access_token?: string };
    expect(data.access_token).toBe("new-access");
  });

  it("accepts callers without fetch metadata when the marker is present", async () => {
    const response = await POST(makeRequest({ "x-token-refresh": "1" }));
    expect(response.status).toBe(200);
  });

  it("still 401s without a refresh cookie", async () => {
    const response = await POST(
      makeRequest({ "sec-fetch-site": "same-origin", "x-token-refresh": "1" }, false),
    );
    expect(response.status).toBe(401);
  });
});
