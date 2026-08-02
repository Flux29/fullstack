import { test as setup, expect } from "@playwright/test";
import path from "path";

const authFile = path.join(__dirname, "../.playwright/.auth/user.json");

/**
 * Authentication setup - runs before all tests.
 *
 * This creates an authenticated session that other tests can reuse.
 */
setup("authenticate", async ({ page }) => {
  // Test credentials - adjust for your environment
  const testEmail = process.env.TEST_USER_EMAIL || "test@example.com";
  const testPassword = process.env.TEST_USER_PASSWORD || "TestPassword123!";

  // Navigate to login page
  await page.goto("/login");

  // Fill in login form
  await page.getByLabel(/email/i).fill(testEmail);
  await page.getByLabel(/password/i).fill(testPassword);

  // Submit and wait for redirect
  await page.getByRole("button", { name: /sign in|log in|login/i }).click();

  // Require the post-login route and the server-managed session cookies. Public
  // landing-page copy is not a reliable authentication signal.
  await expect(page).toHaveURL(/\/(dashboard|chat)/, { timeout: 10000 });
  await expect
    .poll(async () => (await page.context().cookies()).map((cookie) => cookie.name))
    .toEqual(expect.arrayContaining(["access_token", "refresh_token"]));

  // Keep the consent dialog from covering chat controls in tests that reuse
  // this state; optional tracking remains disabled.
  await page.evaluate(() => {
    localStorage.setItem(
      "cookie.consent",
      JSON.stringify({
        essential: true,
        analytics: false,
        functional: false,
        decided_at: new Date().toISOString(),
      }),
    );
  });

  // Save authentication state
  await page.context().storageState({ path: authFile });
});
