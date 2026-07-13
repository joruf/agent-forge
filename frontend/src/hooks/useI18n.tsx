import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  createTranslator,
  DEFAULT_LOCALE,
  localeToIntl,
  resolveLocale,
  type Locale,
} from "../i18n";

const STORAGE_KEY = "agentforge-language";

interface I18nContextValue {
  locale: Locale;
  intlLocale: string;
  setLocale: (locale: Locale) => void;
  t: (key: string, params?: Record<string, string | number>) => string;
}

const I18nContext = createContext<I18nContextValue | null>(null);

interface I18nProviderProps {
  children: ReactNode;
  initialLocale?: Locale;
}

export function I18nProvider({ children, initialLocale }: I18nProviderProps) {
  const [locale, setLocaleState] = useState<Locale>(() => {
    if (initialLocale) {
      return initialLocale;
    }
    return resolveLocale(localStorage.getItem(STORAGE_KEY));
  });

  useEffect(() => {
    document.documentElement.lang = locale;
    localStorage.setItem(STORAGE_KEY, locale);
  }, [locale]);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
  }, []);

  const value = useMemo<I18nContextValue>(() => {
    const t = createTranslator(locale);
    return {
      locale,
      intlLocale: localeToIntl(locale),
      setLocale,
      t,
    };
  }, [locale, setLocale]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const context = useContext(I18nContext);
  if (!context) {
    throw new Error("useI18n must be used within I18nProvider");
  }
  return context;
}

export function getStoredLocale(): Locale {
  return resolveLocale(localStorage.getItem(STORAGE_KEY));
}

export function hasStoredLocale(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) !== null;
  } catch {
    return false;
  }
}

export { DEFAULT_LOCALE, STORAGE_KEY };
