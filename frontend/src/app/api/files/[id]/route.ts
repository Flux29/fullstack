import { NextRequest, NextResponse } from "next/server";
import { FILE_PROXY_JSON_HEADERS } from "@/lib/proxy-headers";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";

export async function GET(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const accessToken = request.cookies.get("access_token")?.value;
    if (!accessToken) {
      return NextResponse.json(
        { detail: "Not authenticated" },
        { status: 401, headers: FILE_PROXY_JSON_HEADERS },
      );
    }

    // Forward ?disposition=attachment so the explicit Download button can
    // force a save dialog. Default (inline) lets the preview iframe render.
    const qs = request.nextUrl.searchParams.toString();
    const url = `${BACKEND_URL}/api/v1/files/${id}${qs ? `?${qs}` : ""}`;
    const response = await fetch(url, {
      headers: {
        Authorization: `Bearer ${accessToken}`,
      },
    });

    if (!response.ok) {
      return NextResponse.json(
        { detail: "File not found" },
        { status: response.status, headers: FILE_PROXY_JSON_HEADERS },
      );
    }

    const data = await response.arrayBuffer();
    const contentType = response.headers.get("content-type") || "application/octet-stream";
    const disposition = response.headers.get("content-disposition") || "";

    return new NextResponse(data, {
      headers: {
        "Content-Type": contentType,
        "Content-Disposition": disposition,
        "Cache-Control": "private, max-age=3600",
        "X-Content-Type-Options": "nosniff",
        // Override the global X-Frame-Options: DENY from next.config.ts so
        // the chat file-preview panel can embed PDFs / HTML in an iframe
        // from the same origin. Without this, Firefox refuses to render.
        "X-Frame-Options": response.headers.get("x-frame-options") || "SAMEORIGIN",
        // The backend decides the CSP per file type (a `sandbox` policy for
        // active content such as HTML/SVG/XML, so uploaded pages cannot run
        // scripts on this origin). Forward it; fail closed if it is missing.
        "Content-Security-Policy":
          response.headers.get("content-security-policy") || "sandbox; frame-ancestors 'self'",
      },
    });
  } catch {
    return NextResponse.json(
      { detail: "Internal server error" },
      { status: 500, headers: FILE_PROXY_JSON_HEADERS },
    );
  }
}
