import { NextRequest, NextResponse } from "next/server";
import { clearAuthCookies, setAuthCookies } from "@/lib/auth-cookies";
import { backendFetch, BackendApiError } from "@/lib/server-api";
import type { RefreshTokenResponse } from "@/types";

export async function POST(request: NextRequest) {
  try {
    // Defense in depth: this route turns the HttpOnly refresh cookie into an
    // access token in the response body, so gate it on browser fetch
    // metadata. Sec-Fetch-Site must be same-origin when the browser sends it
    // (cross-site callers and sandboxed opaque-origin documents report
    // cross-site), and the app's own callers mark themselves with
    // X-Token-Refresh. Neither stops a full same-origin XSS — nothing can
    // while the chat WebSocket needs the token in JS — but they block
    // cross-site and sandboxed callers outright.
    const secFetchSite = request.headers.get("sec-fetch-site");
    if (secFetchSite && secFetchSite !== "same-origin") {
      return NextResponse.json({ detail: "Forbidden" }, { status: 403 });
    }
    if (request.headers.get("x-token-refresh") !== "1") {
      return NextResponse.json({ detail: "Forbidden" }, { status: 403 });
    }

    const refreshToken = request.cookies.get("refresh_token")?.value;

    if (!refreshToken) {
      return NextResponse.json({ detail: "No refresh token" }, { status: 401 });
    }

    const data = await backendFetch<RefreshTokenResponse>("/api/v1/auth/refresh", {
      method: "POST",
      body: JSON.stringify({ refresh_token: refreshToken }),
    });

    const response = NextResponse.json({
      access_token: data.access_token,
      message: "Token refreshed",
    });

    // The refresh token is only re-set when the backend rotated it.
    setAuthCookies(response, {
      accessToken: data.access_token,
      refreshToken: data.refresh_token,
    });

    return response;
  } catch (error) {
    if (error instanceof BackendApiError) {
      const response = NextResponse.json({ detail: "Session expired" }, { status: 401 });

      clearAuthCookies(response);

      return response;
    }
    return NextResponse.json({ detail: "Internal server error" }, { status: 500 });
  }
}
