import {
  MEMORY_TOKEN_OPTIONS,
  formatMemoryTokens,
  normalizeMemoryTokens,
  type MemoryTokenOption,
} from "../constants/memory";
import { useI18n } from "../hooks/useI18n";

interface MemoryTokenSelectProps {
  id?: string;
  name?: string;
  value: number;
  onChange: (value: MemoryTokenOption) => void;
}

export function MemoryTokenSelect({
  id,
  name,
  value,
  onChange,
}: MemoryTokenSelectProps) {
  const { intlLocale } = useI18n();
  const normalized = normalizeMemoryTokens(value);

  return (
    <select
      id={id}
      name={name}
      value={normalized}
      onChange={(event) => onChange(Number(event.target.value) as MemoryTokenOption)}
    >
      {MEMORY_TOKEN_OPTIONS.map((option) => (
        <option key={option} value={option}>
          {formatMemoryTokens(option, intlLocale)}
        </option>
      ))}
    </select>
  );
}
