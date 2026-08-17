import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["**/*.{test,spec}.{ts,tsx}"],
    exclude: ["node_modules", ".next", "e2e"],
    coverage: {
      provider: "v8",
      // lcov feeds the self-hosted Codecov upload in CI (coverage/lcov.info).
      reporter: ["text", "json", "html", "lcov"],
      exclude: ["node_modules", ".next", "e2e", "**/*.d.ts", "**/*.config.*", "vitest.setup.ts"],
      // Ratcheted floors: first CI run (2026-08-05) measured 3.7% statements /
      // 17.4% branches / 6.3% functions / 3.7% lines — the generator's 100%
      // defaults were never met in practice. Raise only alongside real new
      // tests; never lower.
      thresholds: {
        statements: 3,
        branches: 17,
        functions: 6,
        lines: 3,
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
