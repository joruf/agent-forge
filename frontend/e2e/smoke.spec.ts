import { test, expect } from "@playwright/test";

test.describe("AgentForge UI smoke", () => {
  test("shows offline screen when backend is unreachable", async ({ page }) => {
    await page.addInitScript(() => {
      window.localStorage.setItem("agentforge.locale", "en");
    });
    await page.goto("/");
    await expect(page.locator(".offline-screen h1")).toHaveText("AgentForge");
    await expect(page.getByText("Backend is not reachable.")).toBeVisible({
      timeout: 15000,
    });
    await expect(page.getByRole("button", { name: "Reconnect" })).toBeVisible();
  });
});
