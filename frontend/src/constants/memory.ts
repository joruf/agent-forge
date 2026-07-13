export const MEMORY_TOKEN_OPTIONS = [
  100, 250, 500, 1000, 2000, 4000, 8000, 16000, 32000, 64000, 128000,
] as const;

export type MemoryTokenOption = (typeof MEMORY_TOKEN_OPTIONS)[number];

export const DEFAULT_MEMORY_TOKENS: MemoryTokenOption = 32000;

/**
 * Return the closest supported memory token value.
 */
export function normalizeMemoryTokens(value: number): MemoryTokenOption {
  return MEMORY_TOKEN_OPTIONS.reduce((closest, current) =>
    Math.abs(current - value) < Math.abs(closest - value) ? current : closest,
  );
}

/**
 * Format token count for select labels.
 */
export function formatMemoryTokens(value: number, intlLocale = "en-US"): string {
  const formatted = value.toLocaleString(intlLocale);
  return intlLocale.startsWith("de")
    ? `${formatted} Tokens`
    : `${formatted} tokens`;
}
