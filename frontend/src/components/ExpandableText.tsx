import { useState } from "react";
import { useI18n } from "../hooks/useI18n";

interface ExpandableTextProps {
  text: string;
  previewLength?: number;
  className?: string;
}

export function ExpandableText({
  text,
  previewLength = 300,
  className = "",
}: ExpandableTextProps) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const needsTruncate = text.length > previewLength;
  const display = expanded || !needsTruncate
    ? text
    : `${text.slice(0, previewLength)}…`;

  return (
    <div className={`expandable-text ${className}`}>
      <pre>{display}</pre>
      {needsTruncate && (
        <button
          type="button"
          className="expand-btn"
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? t("expandable.showLess") : t("expandable.showMore")}
        </button>
      )}
    </div>
  );
}
