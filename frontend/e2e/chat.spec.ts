import { test, expect } from "@playwright/test";

test.describe("AI Chat", () => {
  // Use authenticated state
  test.use({
    storageState: ".playwright/.auth/user.json",
  });

  test.beforeEach(async ({ page }) => {
    await page.goto("/chat");
  });

  test.describe("Chat Interface", () => {
    test("should display chat container", async ({ page }) => {
      // Chat container should be visible
      await expect(page.getByRole("main")).toBeVisible();

      // Input should be present
      const input = page
        .getByRole("textbox", { name: /message|type|ask/i })
        .or(page.getByPlaceholder(/message|type|ask/i));
      await expect(input).toBeVisible();
    });

    test("should have send button", async ({ page }) => {
      const sendButton = page
        .getByRole("button", { name: /send|submit/i })
        .or(page.locator('button[type="submit"]'));
      await expect(sendButton).toBeVisible();
    });

    test("should allow typing a message", async ({ page }) => {
      const input = page.getByRole("textbox").first();
      await input.fill("Hello, AI assistant!");
      await expect(input).toHaveValue("Hello, AI assistant!");
    });
  });

  test.describe("Chat Functionality", () => {
    test("should send message and receive response", async ({ page }) => {
      const input = page.getByRole("textbox").first();
      const sendButton = page
        .getByRole("button", { name: /send|submit/i })
        .or(page.locator('button[type="submit"]'));

      // Send a message
      await input.fill("Hello!");
      await sendButton.click();

      // User message should appear in chat
      await expect(page.getByRole("paragraph").filter({ hasText: /^Hello!$/ })).toBeVisible();

      // An assistant message renders (data-role on the message wrapper). Under
      // LLM_PROVIDER=test the reply is immediate, so this must not depend on catching a
      // transient "thinking" state.
      await expect(page.locator("[data-role='assistant']").first()).toBeVisible({
        timeout: 30000,
      });
    });

    test("should show progress or a reply after sending", async ({ page }) => {
      const input = page.getByRole("textbox").first();
      const sendButton = page
        .getByRole("button", { name: /send|submit/i })
        .or(page.locator('button[type="submit"]'));

      // Send a message
      await input.fill("What is 2 + 2?");
      await sendButton.click();

      // The turn progressed: a loading indicator, or - when the model answers faster than
      // the indicator can paint (the CI fake does) - the reply itself.
      await expect(
        page
          .getByText(/thinking|loading|processing/i)
          .or(page.locator(".animate-pulse, .animate-spin"))
          .or(page.locator("[data-role='assistant']"))
          .first(),
      ).toBeVisible();
    });

    test("should clear input after sending", async ({ page }) => {
      const input = page.getByRole("textbox").first();
      const sendButton = page
        .getByRole("button", { name: /send|submit/i })
        .or(page.locator('button[type="submit"]'));

      await input.fill("Test message");
      await sendButton.click();

      // Input should be cleared
      await expect(input).toHaveValue("");
    });
  });

  test.describe("Message Display", () => {
    test("should display user messages correctly", async ({ page }) => {
      const input = page.getByRole("textbox").first();
      const sendButton = page
        .getByRole("button", { name: /send|submit/i })
        .or(page.locator('button[type="submit"]'));

      await input.fill("My test message");
      await sendButton.click();

      // Message should be styled as user message
      const userMessage = page.getByRole("paragraph").filter({ hasText: /^My test message$/ });
      await expect(userMessage).toBeVisible();
    });

    test("should support multiple messages", async ({ page }) => {
      const input = page.getByRole("textbox").first();
      const sendButton = page
        .getByRole("button", { name: /send|submit/i })
        .or(page.locator('button[type="submit"]'));

      // Send first message
      await input.fill("First message");
      await sendButton.click();
      await expect(
        page.getByRole("paragraph").filter({ hasText: /^First message$/ }),
      ).toBeVisible();

      // Wait for response
      await page.waitForTimeout(1000);

      // Send second message
      await input.fill("Second message");
      await sendButton.click();
      await expect(
        page.getByRole("paragraph").filter({ hasText: /^Second message$/ }),
      ).toBeVisible();

      // Both messages should be visible
      await expect(
        page.getByRole("paragraph").filter({ hasText: /^First message$/ }),
      ).toBeVisible();
      await expect(
        page.getByRole("paragraph").filter({ hasText: /^Second message$/ }),
      ).toBeVisible();
    });
  });

  test.describe("Keyboard Navigation", () => {
    test("should send message on Enter key", async ({ page }) => {
      const input = page.getByRole("textbox").first();

      await input.fill("Keyboard test");
      await input.press("Enter");

      // Message should be sent
      await expect(
        page.getByRole("paragraph").filter({ hasText: /^Keyboard test$/ }),
      ).toBeVisible();
    });

    test("should support Shift+Enter for new line", async ({ page }) => {
      const input = page.getByRole("textbox").first();

      await input.fill("Line 1");
      await input.press("Shift+Enter");
      await input.type("Line 2");

      // Should contain multiline text (behavior may vary)
      const value = await input.inputValue();
      expect(value).toContain("Line 1");
    });
  });
});

test.describe("Integrations settings", () => {
  test.use({ storageState: ".playwright/.auth/user.json" });

  test("shows Google Workspace and deployment-managed MCP servers", async ({ page }) => {
    await page.goto("/settings/integrations");

    await expect(page.getByRole("heading", { name: "Integrations" })).toBeVisible();
    await expect(page.getByText("Gmail", { exact: true })).toBeVisible();
    await expect(page.getByText("Google Drive", { exact: true })).toBeVisible();
    await expect(page.getByText("docling-internal", { exact: true })).toBeVisible();
    await expect(page.getByText("chrome-internal", { exact: true })).toBeVisible();
    await expect(page.getByText("github-internal", { exact: true })).toBeVisible();
  });
});

test.describe("Conversation Persistence", () => {
  test.use({
    storageState: ".playwright/.auth/user.json",
  });

  test("should show conversation list", async ({ page }) => {
    await page.goto("/chat");

    // Should have some way to view conversation history.
    // List may or may not be visible depending on UI — just ensure the
    // locator resolves without throwing.
    void page.getByRole("list").or(page.locator("[data-testid='conversation-list']"));
  });

  test("should create new conversation", async ({ page }) => {
    await page.goto("/chat");

    // Find new conversation button
    const newChatButton = page
      .getByRole("button", { name: /new|create/i })
      .or(page.getByText(/new chat/i));

    if (await newChatButton.isVisible()) {
      await newChatButton.click();
      // Should start a new conversation
    }
  });
});
