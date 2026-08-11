import { test, expect } from "@playwright/test";

test.describe("Google integrations", () => {
  test.use({ storageState: ".playwright/.auth/user.json" });

  test("shows all generally available Google Workspace integrations", async ({ page }) => {
    await page.goto("/settings/integrations");

    await expect(page.getByRole("heading", { name: "Integrations" })).toBeVisible();
    await expect(page.getByText("send approved messages", { exact: false })).toBeVisible();
    await expect(page.getByText("standard Calendar API", { exact: false })).toBeVisible();
    await expect(page.getByText("standard Drive API", { exact: false })).toBeVisible();
    await expect(page.getByText("standard Docs API", { exact: false })).toBeVisible();
    await expect(page.getByText("standard Sheets API", { exact: false })).toBeVisible();
    await expect(page.getByText("standard Slides API", { exact: false })).toBeVisible();
    await expect(page.getByText("standard Chat API", { exact: false })).toBeVisible();
    await expect(page.getByText("People API", { exact: false })).toBeVisible();
    await expect(page.getByText("Developer Preview", { exact: false })).toHaveCount(0);
  });

  test("offers an atomic standard API upgrade for a preview connection", async ({ page }) => {
    await page.route("**/api/me/mcp-connections**", (route) => {
      if (route.request().url().endsWith("/workspace")) {
        return route.fulfill({ json: { items: [], total: 0 } });
      }
      return route.fulfill({
        json: {
          items: [
            {
              id: "legacy-drive",
              name: "google-drive",
              url: "https://drivemcp.googleapis.com/mcp/v1",
              has_auth_token: true,
              allowed_tools: ["search_files"],
              is_enabled: true,
              auth_type: "oauth",
              oauth_authorized: true,
              last_status: "ok",
              last_error: null,
              last_checked_at: null,
              created_at: "2026-08-03T00:00:00Z",
              updated_at: null,
            },
          ],
          total: 1,
        },
      });
    });

    await page.goto("/settings/integrations");

    await expect(page.getByRole("button", { name: "Upgrade to standard API" })).toBeVisible();
  });
});
