import { describe, expect, it } from "vitest";
import { createTranslator, isLocale, resolveLocale } from "./index";

describe("i18n locale helpers", () => {
  it("resolves supported locales and falls back to English", () => {
    expect(resolveLocale("de")).toBe("de");
    expect(resolveLocale("en")).toBe("en");
    expect(resolveLocale("fr")).toBe("en");
    expect(resolveLocale(null)).toBe("en");
  });

  it("detects locale codes", () => {
    expect(isLocale("de")).toBe(true);
    expect(isLocale("en")).toBe(true);
    expect(isLocale("es")).toBe(false);
  });

  it("translates known keys and interpolates parameters", () => {
    const t = createTranslator("en");
    expect(t("app.title")).toBe("AgentForge");
    expect(t("settings.modelsHint", { models: "3", installed: "2" })).toContain("3");
  });
});
