import type { SetupTestReport } from "../types";

function statusIcon(ok: boolean | null | undefined, skipped?: boolean, warning?: boolean): string {
  if (skipped) return "○";
  if (warning) return "⚠";
  if (ok === true) return "✓";
  if (ok === false) return "✗";
  return "·";
}

function statusClass(ok: boolean | null | undefined, skipped?: boolean, warning?: boolean): string {
  if (skipped) return "test-skipped";
  if (warning) return "test-warning";
  if (ok === true) return "test-ok";
  if (ok === false) return "test-fail";
  return "test-pending";
}

interface TestResultsListProps {
  report: SetupTestReport;
  filter?: string[];
}

export function TestResultsList({ report, filter }: TestResultsListProps) {
  const results = filter
    ? report.results.filter((result) => filter.includes(result.id))
    : report.results;

  if (results.length === 0) {
    return null;
  }

  return (
    <ul className="setup-test-results">
      {results.map((result) => (
        <li key={result.id} className={statusClass(result.ok, result.skipped, result.warning)}>
          <span className="test-icon">{statusIcon(result.ok, result.skipped, result.warning)}</span>
          <div className="test-content">
            <strong>{result.label}</strong>
            <span>{result.message}</span>
            {result.models && result.models.length > 0 && (
              <span className="test-models">
                {result.models.slice(0, 8).join(", ")}
                {result.models.length > 8 ? ` (+${result.models.length - 8})` : ""}
              </span>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}
