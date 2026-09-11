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

/**
 * BCP-47 tag per language: what Intl and document.documentElement.lang receive.
 * useFormat() formats every date and number with these.
 */
export const LOCALE_TAGS: Record<Lang, string> = {
  en: 'en-US',
  fr: 'fr-FR',
  de: 'de-DE',
  es: 'es-ES',
  pt: 'pt-BR',
  nl: 'nl-NL',
  ja: 'ja-JP',
  zh: 'zh-CN',
};

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

/** The translate function as `useT()` hands it out, for code that takes it as an argument. */
export type TranslateFn = (key: string, params?: Record<string, string | number>) => string;

interface I18nContextValue {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: TranslateFn;
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
  // `t()` falls back to the key itself, so anything painted before the first locale chunk
  // lands reads `nav.dashboard`, `ui.loading`, `dashboard.page_description`. The provider
  // wraps the whole app, so that is every screen, and it looks like a half-deployed build --
  // the natural reaction being to reload or roll back a healthy release. Non-English users
  // saw it on every cold load, their chunk never being the one already parsed.
  //
  // The cache is read here rather than waited for: a second mount at the same language has
  // the map in hand and must not blank the screen again.
  const [translations, setTranslations] = useState<Translations>(() => cache[lang] ?? {});
  // One-way, and deliberately not `isLoading`: that goes true again on every language
  // switch, and blanking the app mid-session would be worse than holding the previous
  // language until the new one lands -- which is what `setLang` already arranges.
  const [ready, setReady] = useState(() => cache[lang] !== undefined);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    loadTranslations(lang).then((t) => {
      if (cancelled) return;
      setTranslations(t);
      setIsLoading(false);
      setReady(true);
    });

    return () => {
      cancelled = true;
    };
  }, [lang]);

  // Screen readers pick their voice, and browsers their hyphenation and CJK glyphs, from
  // the document language; index.html ships with "en" and Vite never rewrites it.
  useEffect(() => {
    document.documentElement.lang = LOCALE_TAGS[lang];
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

  // Deliberately text-free: any label here would need a translation that is, by definition,
  // not loaded yet. The background token matches the blocking theme script in index.html, so
  // the first frame already carries the right colours and nothing flashes. The cost is one
  // small same-origin JSON serialised ahead of the app's first request, which is cheaper than
  // showing the operator a screen full of raw keys.
  if (!ready) return <div className="min-h-screen bg-background" aria-busy="true" />;

  return <I18nContext.Provider value={{ lang, setLang, t, isLoading }}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  return useContext(I18nContext);
}

/** Shorthand hook — just `const t = useT()` */
export function useT() {
  return useContext(I18nContext).t;
}
