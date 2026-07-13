import de from "./locales/de.json";
import en from "./locales/en.json";

export type Locale = "en" | "de";

export const LOCALES: { code: Locale; label: string }[] = [
  { code: "en", label: "English" },
  { code: "de", label: "Deutsch" },
];

export const DEFAULT_LOCALE: Locale = "en";

const catalogs: Record<Locale, Record<string, unknown>> = { en, de };

export function isLocale(value: string): value is Locale {
  return value === "en" || value === "de";
}

export function resolveLocale(value: string | null | undefined): Locale {
  return value && isLocale(value) ? value : DEFAULT_LOCALE;
}

export function createTranslator(locale: Locale) {
  return function t(key: string, params?: Record<string, string | number>): string {
    const parts = key.split(".");
    let node: unknown = catalogs[locale];
    for (const part of parts) {
      if (node && typeof node === "object" && part in (node as object)) {
        node = (node as Record<string, unknown>)[part];
      } else {
        node = undefined;
        break;
      }
    }

    let text = typeof node === "string" ? node : key;
    if (params) {
      for (const [name, value] of Object.entries(params)) {
        text = text.split(`{{${name}}}`).join(String(value));
      }
    }
    return text;
  };
}

export function localeToIntl(locale: Locale): string {
  return locale === "de" ? "de-DE" : "en-US";
}
