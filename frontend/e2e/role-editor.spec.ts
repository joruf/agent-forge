import { test, expect } from "@playwright/test";
import { seedEnglishLocale, setupMockBackend } from "./helpers/mockBackend";

test.describe("AgentForge role editor", () => {
  test.beforeEach(async ({ page }) => {
    await seedEnglishLocale(page);
    await setupMockBackend(page);
  });

  async function openAgentsTab(page: import("@playwright/test").Page) {
    await page.goto("/");
    await expect(page.locator(".app")).toBeVisible({ timeout: 15000 });

    await page.getByRole("button", { name: "Open menu" }).click();
    await page.getByRole("menuitem", { name: "Properties" }).click();
    await expect(page.locator(".settings-modal")).toBeVisible();

    await page.getByRole("tab", { name: "Agents" }).click();
    await expect(page.locator(".roles-editor")).toBeVisible();
  }

  test("creates, edits, and deletes a custom role", async ({ page }) => {
    await openAgentsTab(page);

    await page.getByRole("button", { name: "Create role" }).click();
    await expect(page.getByText("New custom role")).toBeVisible();

    await page.getByLabel("Role ID").fill("data_analyst");
    await page.getByLabel("Display name").fill("Data Analyst");
    await page.getByLabel("Description").fill("Analyzes datasets and reports findings.");
    await page.getByLabel("System prompt / instructions").fill(
      "You are a data analyst. Summarize trends clearly.",
    );

    await page.locator(".role-form-actions").getByRole("button", { name: "Save" }).click();

    await expect(page.getByText("Data Analyst")).toBeVisible();
    await expect(page.getByText("data_analyst")).toBeVisible();
    await expect(page.getByText("No custom roles yet")).toHaveCount(0);

    await page
      .locator(".role-card--custom")
      .filter({ hasText: "Data Analyst" })
      .getByRole("button", { name: "Edit" })
      .click();
    await expect(page.getByText("Edit custom role")).toBeVisible();

    await page.getByLabel("Display name").fill("Senior Data Analyst");
    await page.getByLabel("Description").fill("Senior analyst for complex datasets.");
    await page.locator(".role-form-actions").getByRole("button", { name: "Save" }).click();

    await expect(page.getByText("Senior Data Analyst")).toBeVisible();
    await expect(page.getByText("Senior analyst for complex datasets.")).toBeVisible();

    page.once("dialog", (dialog) => {
      void dialog.accept();
    });
    await page
      .locator(".role-card--custom")
      .filter({ hasText: "Senior Data Analyst" })
      .getByRole("button", { name: "Delete" })
      .click();

    await expect(page.getByText("Senior Data Analyst")).toHaveCount(0);
    await expect(page.getByText("No custom roles yet")).toBeVisible();
  });

  test("shows built-in roles as read-only", async ({ page }) => {
    await openAgentsTab(page);

    await expect(page.getByText("Built-in roles (3)")).toBeVisible();
    await expect(page.locator(".role-card--builtin").filter({ hasText: "Developer" })).toBeVisible();
    await expect(
      page.locator(".role-card--builtin").filter({ hasText: "Developer" }).getByRole("button", {
        name: "Edit",
      }),
    ).toHaveCount(0);
    await expect(
      page.locator(".role-card--builtin").filter({ hasText: "Developer" }).getByRole("button", {
        name: "Delete",
      }),
    ).toHaveCount(0);
  });
});
