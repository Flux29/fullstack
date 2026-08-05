import { NextResponse, type NextRequest } from "next/server";

import { backendFetch } from "@/lib/server-api";

/**
 * OAuth redirect target. The provider sends the user here with `code` + `state`
 * (or an `error`). We forward them to the backend's state-authenticated
 * callback, then bounce the browser back to the integrations settings page
 * with a status the page turns into a toast. No auth cookie is required —
 * the `state` token authenticates the exchange.
 */
export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  // In the standalone Docker image Next binds to 0.0.0.0, and request.url can
  // therefore use that non-routable bind address even when the browser reached
  // localhost. Always return the browser to the configured public site origin.
  const publicOrigin = process.env.NEXT_PUBLIC_SITE_URL || request.nextUrl.origin;
  const settings = (query: string) =>
    NextResponse.redirect(new URL(`/settings/integrations?${query}`, publicOrigin));

  const providerError = params.get("error");
  if (providerError) {
    const reason = params.get("error_description") ?? providerError;
    return settings(`mcp_oauth=error&reason=${encodeURIComponent(reason)}`);
  }

  const code = params.get("code");
  const state = params.get("state");
  if (!code || !state) {
    return settings(`mcp_oauth=error&reason=${encodeURIComponent("Missing authorization code")}`);
  }

  try {
    const result = await backendFetch<{
      ok: boolean;
      connection_name: string | null;
      error: string | null;
    }>("/api/v1/me/mcp-connections/oauth/callback", {
      method: "POST",
      body: JSON.stringify({ code, state }),
    });
    if (!result.ok) {
      const reason = result.error ?? "Authorization failed";
      return settings(`mcp_oauth=error&reason=${encodeURIComponent(reason)}`);
    }
    const name = result.connection_name ?? "";
    return settings(`mcp_oauth=success&name=${encodeURIComponent(name)}`);
  } catch {
    return settings(`mcp_oauth=error&reason=${encodeURIComponent("Authorization failed")}`);
  }
}
