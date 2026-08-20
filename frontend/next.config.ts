import withBundleAnalyzer from "@next/bundle-analyzer";
import createMDX from "@next/mdx";
import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/i18n.ts");
const withMDX = createMDX({
  // No extra remark/rehype plugins for now — keep build simple.
  // next-mdx-remote/rsc handles the actual blog post compilation.
});

// Bundle analyzer — only active when ANALYZE=true (e.g. `bun run analyze`).
const withAnalyzer = withBundleAnalyzer({
  enabled: process.env.ANALYZE === "true",
});

// Content Security Policy directives.
//
// Environment split: 'unsafe-eval' and the permissive localhost/ws connect
// sources exist only for dev tooling (HMR, eval sourcemaps) and never reach a
// production build. 'unsafe-inline' for scripts is still required by Next's
// hydration bootstrap; removing it means the per-request nonce architecture
// (headers set in middleware, which forces dynamic rendering on every route)
// and is tracked as a follow-up, not attempted here.
const isDev = process.env.NODE_ENV === "development";

// The chat WebSocket is the documented proxy exception: it connects to the
// backend origin directly, so the production policy must name that origin.
// NEXT_PUBLIC_WS_URL is a build argument, so it is known at config time.
const wsOrigin = (() => {
  try {
    const url = process.env.NEXT_PUBLIC_WS_URL;
    return url ? new URL(url).origin.replace(/^http/, "ws") : "";
  } catch {
    return "";
  }
})();

const _frameAncestors = "frame-ancestors 'none';";

const scriptSrc = isDev ? "'self' 'unsafe-eval' 'unsafe-inline'" : "'self' 'unsafe-inline'";
const connectSrc = isDev
  ? "'self' ws: wss: http://localhost:* https://localhost:*"
  : `'self' ${wsOrigin || "wss:"}`;

const ContentSecurityPolicy = `
  default-src 'self';
  script-src ${scriptSrc};
  style-src 'self' 'unsafe-inline';
  img-src 'self' blob: data: https:;
  font-src 'self' data:;
  connect-src ${connectSrc};
  ${_frameAncestors}
  base-uri 'self';
  form-action 'self';
`
  .replace(/\n/g, " ")
  .trim();

const securityHeaders = [
  {
    key: "Content-Security-Policy",
    value: ContentSecurityPolicy,
  },
  {
    key: "X-Content-Type-Options",
    value: "nosniff",
  },
  {
    key: "X-Frame-Options",
    value: "DENY",
  },
  {
    key: "X-XSS-Protection",
    value: "1; mode=block",
  },
  {
    key: "Referrer-Policy",
    value: "strict-origin-when-cross-origin",
  },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=()",
  },
];

const nextConfig: NextConfig = {
  output: "standalone",
  pageExtensions: ["ts", "tsx", "mdx"],

  // Security headers. /api/files is excluded from the catch-all so its route
  // handler alone owns those headers: it forwards the backend's
  // per-file-type policy (X-Frame-Options: SAMEORIGIN for the same-origin
  // preview iframe, plus a `sandbox` CSP for active content such as HTML/SVG
  // so uploaded documents cannot run scripts on this origin). A config-level
  // rule cannot express that per-type distinction, and two sources competing
  // for the same header keys is exactly how the sandbox policy got silently
  // replaced before.
  async headers() {
    return [
      {
        source: "/((?!api/files/).*)",
        headers: securityHeaders,
      },
    ];
  },

  // Environment variables available on the server side only
  serverRuntimeConfig: {
    apiUrl: process.env.BACKEND_URL || "http://localhost:8100",
  },

  // Environment variables available on both server and client
  publicRuntimeConfig: {
    appName: "fullstack",
  },
};
export default withAnalyzer(withNextIntl(withMDX(nextConfig)));
