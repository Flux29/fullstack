import { NextRequest } from "next/server";

import { BACKEND_URL } from "@/lib/server-api";

// SSE must never be cached or statically optimized.
export const dynamic = "force-dynamic";

/**
 * Proxy for the backend RAG status SSE stream. EventSource cannot send an
 * Authorization header, so the browser connects same-origin here and the bearer
 * token from the HttpOnly cookie is injected server-side.
 */
export async function GET(request: NextRequest) {
  const accessToken = request.cookies.get("access_token")?.value;
  if (!accessToken) {
    return new Response(JSON.stringify({ detail: "Not authenticated" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
  }

  const upstream = await fetch(`${BACKEND_URL}/api/v1/rag/status/stream`, {
    headers: {
      Authorization: `Bearer ${accessToken}`,
      Accept: "text/event-stream",
    },
    cache: "no-store",
    signal: request.signal,
  });

  if (!upstream.ok || !upstream.body) {
    return new Response(JSON.stringify({ detail: "Stream unavailable" }), {
      status: upstream.status || 502,
      headers: { "Content-Type": "application/json" },
    });
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}
