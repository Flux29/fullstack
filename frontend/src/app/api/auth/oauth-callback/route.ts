import { NextRequest, NextResponse } from "next/server";

import { setAuthCookies } from "@/lib/auth-cookies";
import {
  backendFetch,
  BackendApiError,
  getClientIpHeaders,
  backendErrorDetail,
} from "@/lib/server-api";
import type { LoginResponse } from "@/types";

interface OAuthCallbackBody {
  code: string;
}

/**
 * Completes OAuth sign-in. The browser hands over the single-use code from the
 * backend redirect; the JWT pair is fetched server-to-server here and only ever
 * stored in HttpOnly cookies — tokens never transit a URL or the client bundle
 * beyond the access token needed for the chat WebSocket.
 */
export async function POST(request: NextRequest) {
  try {
    const body = (await request.json()) as Partial<OAuthCallbackBody>;
    if (!body.code) {
      return NextResponse.json({ detail: "Missing sign-in code" }, { status: 400 });
    }

    const tokens = await backendFetch<LoginResponse>("/api/v1/oauth/exchange", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...getClientIpHeaders(request),
      },
      body: JSON.stringify({ code: body.code }),
    });

    const user = await backendFetch("/api/v1/auth/me", {
      headers: { Authorization: `Bearer ${tokens.access_token}` },
    });

    const response = NextResponse.json({
      user,
      access_token: tokens.access_token,
      message: "Sign-in successful",
    });

    setAuthCookies(response, {
      accessToken: tokens.access_token,
      refreshToken: tokens.refresh_token,
    });
    return response;
  } catch (error) {
    if (error instanceof BackendApiError) {
      const detail = backendErrorDetail(error, "Sign-in failed");
      return NextResponse.json({ detail }, { status: error.status });
    }
    return NextResponse.json({ detail: "Internal server error" }, { status: 500 });
  }
}
