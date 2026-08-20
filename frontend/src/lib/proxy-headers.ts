/**
 * Security headers for /api/files/* JSON responses.
 *
 * The next.config.ts catch-all header rule excludes /api/files/ so the file
 * handler can forward the backend's per-file-type policy (config-level rules
 * override handler-set headers and cannot vary by MIME type). That exclusion
 * means JSON error/metadata responses on these routes get no global headers,
 * so they set this equivalent strict set explicitly.
 */
export const FILE_PROXY_JSON_HEADERS: Record<string, string> = {
  "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  "Referrer-Policy": "strict-origin-when-cross-origin",
};
