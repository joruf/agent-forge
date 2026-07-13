/**
 * Format an ISO timestamp for chat and agent history headers.
 *
 * @param iso ISO-8601 date string
 * @param locale BCP 47 locale for formatting
 * @return Localized date and time string
 */
export function formatMessageTimestamp(iso: string, locale: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return "";
  }

  return date.toLocaleString(locale, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}
