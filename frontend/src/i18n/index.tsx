/* eslint-disable react-refresh/only-export-components */
/**
 * Vauxtra i18n — lightweight, zero-dependency translation system.
 *
 * HOW TO CONTRIBUTE A TRANSLATION:
 *  1. Copy `locales/en.json` to `locales/<lang>.json`  (use BCP-47 codes: de, es, pt, nl, ja…)
 *  2. Translate every value (keys stay in English)
 *  3. Add your language to SUPPORTED_LANGUAGES below
 *  4. Open a PR — thank you!
 *
 * Community translation hub: https://github.com/ptitzgeg-on-git/vauxtra/tree/main/frontend/src/locales
 */

import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from 'react';

export type Lang = 'en' | 'fr' | 'de' | 'es' | 'pt' | 'nl' | 'ja' | 'zh';

export const SUPPORTED_LANGUAGES: { code: Lang; label: string; flag: string }[] = [
  { code: 'en', label: 'English',    flag: '🇬🇧' },
  { code: 'fr', label: 'Français',   flag: '🇫🇷' },
  { code: 'de', label: 'Deutsch',    flag: '🇩🇪' },
  { code: 'es', label: 'Español',    flag: '🇪🇸' },
  { code: 'pt', label: 'Português',  flag: '🇧🇷' },
  { code: 'nl', label: 'Nederlands', flag: '🇳🇱' },
  { code: 'ja', label: '日本語',      flag: '🇯🇵' },
  { code: 'zh', label: '中文',        flag: '🇨🇳' },
];

type Translations = Record<string, string>;
const cache: Partial<Record<Lang, Translations>> = {};

async function loadTranslations(lang: Lang): Promise<Translations> {
  if (cache[lang]) return cache[lang]!;
  try {
    const mod = await import(`../locales/${lang}.json`);
    cache[lang] = mod.default as Translations;
    return cache[lang]!;
  } catch {
    // Fallback to English
    if (lang !== 'en') return loadTranslations('en');
    return {};
  }
}

function getKey(obj: Translations, key: string): string | undefined {
  return obj[key];
}

interface I18nContextValue {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: (key: string, params?: Record<string, string | number>) => string;
  isLoading: boolean;
}

const I18nContext = createContext<I18nContextValue>({
  lang: 'en',
  setLang: () => {},
  t: (key) => key,
  isLoading: false,
});

const STORAGE_KEY = 'vauxtra_lang';

function detectBrowserLang(): Lang {
  const raw = navigator.language?.split('-')[0] as Lang;
  return SUPPORTED_LANGUAGES.some((l) => l.code === raw) ? raw : 'en';
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => {
    const stored = localStorage.getItem(STORAGE_KEY) as Lang | null;
    if (stored && SUPPORTED_LANGUAGES.some((l) => l.code === stored)) return stored;
    return detectBrowserLang();
  });
  const [translations, setTranslations] = useState<Translations>({});
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    loadTranslations(lang).then((t) => {
      if (cancelled) return;
      setTranslations(t);
      setIsLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, [lang]);

  const setLang = useCallback((l: Lang) => {
    if (l === lang) return;
    setIsLoading(true);
    localStorage.setItem(STORAGE_KEY, l);
    setLangState(l);
  }, [lang]);

  const t = useCallback(
    (key: string, params?: Record<string, string | number>): string => {
      let val = getKey(translations, key) ?? key;
      if (params) {
        for (const [k, v] of Object.entries(params)) {
          val = val.replace(new RegExp(`\\{${k}\\}`, 'g'), String(v));
        }
      }
      return val;
    },
    [translations],
  );

  return <I18nContext.Provider value={{ lang, setLang, t, isLoading }}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  return useContext(I18nContext);
}

/** Shorthand hook — just `const t = useT()` */
export function useT() {
  return useContext(I18nContext).t;
}
