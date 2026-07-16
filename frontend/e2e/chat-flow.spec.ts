import { test, expect } from "@playwright/test";
import {
  createSampleChat,
  createSampleMessages,
  seedEnglishLocale,
  setupMockBackend,
} from "./helpers/mockBackend";

test.describe("AgentForge chat flow", () => {
  test.beforeEach(async ({ page }) => {
    await seedEnglishLocale(page);
  });

  test("loads with backend online and shows chat area", async ({ page }) => {
    const sampleChat = createSampleChat();
    await setupMockBackend(page, {
      chats: [sampleChat],
      messagesByChat: {
        [sampleChat.id]: createSampleMessages(sampleChat.id),
      },
    });

    await page.goto("/");
    await expect(page.locator(".app")).toBeVisible({ timeout: 15000 });
    await expect(page.locator(".offline-screen")).toHaveCount(0);
    await expect(page.locator(".chat-panel")).toBeVisible();
    await expect(page.getByText("Hello from persisted history")).toBeVisible();
    await expect(page.getByText("Welcome back — mock backend is online.")).toBeVisible();
  });

  test("persists messages after page refresh", async ({ page }) => {
    const sampleChat = createSampleChat();
    await setupMockBackend(page, {
      chats: [sampleChat],
      messagesByChat: {
        [sampleChat.id]: createSampleMessages(sampleChat.id),
      },
    });

    await page.goto("/");
    await expect(page.getByText("Hello from persisted history")).toBeVisible({ timeout: 15000 });

    await page.reload();
    await expect(page.locator(".app")).toBeVisible({ timeout: 15000 });
    await expect(page.locator(".chat-panel")).toBeVisible();
    await expect(page.getByText("Hello from persisted history")).toBeVisible();
    await expect(page.getByText("Welcome back — mock backend is online.")).toBeVisible();
  });

  test("sends a user message and shows assistant reply", async ({ page }) => {
    await setupMockBackend(page);

    await page.goto("/");
    await expect(page.locator(".app")).toBeVisible({ timeout: 15000 });

    await page.getByRole("button", { name: "+ Quick Chat" }).click();
    await expect(page.locator(".chat-panel")).toBeVisible();
    await expect(page.locator(".chat-header .badge")).toHaveText("Quick Chat");

    const textarea = page.locator(".chat-input textarea");
    await textarea.fill("What is AgentForge?");
    await page.getByRole("button", { name: "Send" }).click();

    await expect(page.locator(".message.message-user")).toContainText("What is AgentForge?", {
      timeout: 10000,
    });
    await expect(page.locator(".message.message-assistant")).toContainText(
      "Mock assistant reply for E2E.",
      { timeout: 10000 },
    );
  });

  test("shows prompt correction badge when normalizer adjusts input", async ({ page }) => {
    await setupMockBackend(page, { promptCorrections: true });

    await page.goto("/");
    await expect(page.locator(".app")).toBeVisible({ timeout: 15000 });

    await page.getByRole("button", { name: "+ Quick Chat" }).click();
    await page.locator(".chat-input textarea").fill("rd file README.md");
    await page.getByRole("button", { name: "Send" }).click();

    await expect(page.locator(".message-prompt-corrections")).toBeVisible({ timeout: 10000 });
    await expect(page.getByText("Prompt corrected before processing")).toBeVisible();
    await expect(page.locator(".message-prompt-corrections code").first()).toHaveText("rd file");
    await expect(page.locator(".message-prompt-corrections code").nth(1)).toHaveText("read file");
  });
});
