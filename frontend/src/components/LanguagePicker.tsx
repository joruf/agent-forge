import { useState } from "react";
import { LOCALES, type Locale } from "../i18n";

interface LanguagePickerProps {
  open: boolean;
  onSelect: (locale: Locale) => void;
}

export function LanguagePicker({ open, onSelect }: LanguagePickerProps) {
  const [busy, setBusy] = useState(false);

  if (!open) {
    return null;
  }

  const handleSelect = (locale: Locale) => {
    if (busy) {
      return;
    }
    setBusy(true);
    onSelect(locale);
  };

  return (
    <div className="language-picker-overlay">
      <div className="language-picker">
        <h2>AgentForge</h2>
        <p className="language-picker-lead">
          Choose your interface language
          <br />
          Wählen Sie Ihre Oberflächensprache
        </p>
        <div className="language-picker-actions">
          {LOCALES.map((entry) => (
            <button
              key={entry.code}
              type="button"
              className="language-picker-btn"
              disabled={busy}
              onClick={() => handleSelect(entry.code)}
            >
              {entry.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
