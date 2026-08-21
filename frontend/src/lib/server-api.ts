/**
 * Server-side API client for calling the FastAPI backend.
 * This module is used by Next.js API routes to proxy requests.
 * IMPORTANT: This file should only be imported in server-side code (API routes, Server Components).
 */

// Exported for the rare proxy handler that must stream the raw response
// (e.g. the RAG status SSE proxy) instead of going through backendFetch.
export const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";

export class BackendApiError extends Error {
  constructor(
    public status: number,
    public statusText: string,
    public data?: unknown,
  ) {
    super(`Backend API error: ${status} ${statusText}`);
    this.name = "BackendApiError";
  }
}

/**
 * The backend's structured error envelope. ``error`` is what
 * ``api/exception_handlers.py`` emits for every domain exception; ``detail`` is
 * what FastAPI itself emits for a raw ``HTTPException`` (a string) or a request
 * validation failure (a list of per-field errors).
 */
interface BackendErrorBody {
  error?: { message?: string };
  detail?: string | { msg?: string }[];
}

/**
 * The message a user should actually see for a failed backend call.
 *
 * ``BackendApiError.message`` is only ever "Backend API error: 409 Conflict" —
 * an HTTP status line, which tells a user nothing about what went wrong. The
 * real message is the one the backend worded for them, carried in the parsed
 * body on ``.data``, so prefer that and keep the status line as the fallback.
 *
 * 5xx bodies are deliberately ignored: an unexpected server error is exactly
 * where an unreviewed string could carry internal detail, so those keep the
 * caller's generic message.
 */
export function backendErrorDetail(error: BackendApiError, fallback?: string): string {
  const generic = fallback || error.message;

  if (error.status >= 500) {
    return generic;
  }

  const body = error.data as BackendErrorBody | null | undefined;

  const domainMessage = body?.error?.message;
  if (domainMessage) {
    return domainMessage;
  }

  const detail = body?.detail;
  if (typeof detail === "string" && detail) {
    return detail;
  }
  if (Array.isArray(detail)) {
    const messages = detail.map((entry) => entry?.msg).filter((msg): msg is string => !!msg);
    if (messages.length > 0) {
      return messages.join("; ");
    }
  }

  return generic;
}

interface RequestOptions extends RequestInit {
  params?: Record<string, string>;
  /** Return raw text instead of parsing as JSON */
  raw?: boolean;
}

/**
 * Make a request to the FastAPI backend.
 * This should only be called from Next.js API routes or Server Components.
 */
export async function backendFetch<T>(endpoint: string, options: RequestOptions = {}): Promise<T> {
  const { params, body, raw, ...fetchOptions } = options;

  let url = `${BACKEND_URL}${endpoint}`;

  if (params) {
    const searchParams = new URLSearchParams(params);
    url += `?${searchParams.toString()}`;
  }

  // Determine content type - don't set for FormData (browser will set with boundary)
  const headers: Record<string, string> = {};
  if (body instanceof FormData) {
    // Let the browser set Content-Type with the multipart boundary
  } else {
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(url, {
    ...fetchOptions,
    headers: {
      ...headers,
      ...fetchOptions.headers,
    },
    body,
  });

  if (!response.ok) {
    let errorData;
    try {
      errorData = await response.json();
    } catch {
      errorData = null;
    }
    throw new BackendApiError(response.status, response.statusText, errorData);
  }

  // Handle empty responses
  const text = await response.text();
  if (!text) {
    return null as T;
  }

  if (raw) {
    return text as T;
  }

  return JSON.parse(text);
}

/**
 * Forward authorization header from the incoming request to the backend.
 */
export function getAuthHeaders(authHeader: string | null): Record<string, string> {
  if (!authHeader) {
    return {};
  }
  return { Authorization: authHeader };
}

/**
 * Forward the client address so backend rate limiting keys on the real
 * client rather than this proxy. Trust the existing X-Forwarded-For chain
 * (set by the edge in production); requests that arrive without one came
 * to this server directly and carry no client identity worth forwarding.
 */
export function getClientIpHeaders(request: Request): Record<string, string> {
  const forwarded = request.headers.get("x-forwarded-for");
  if (!forwarded) {
    return {};
  }
  return { "X-Forwarded-For": forwarded };
}
