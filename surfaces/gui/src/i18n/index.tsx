// Zero-dependency i18n for the GUI.
//
// Design: the English source string IS the key. `t("Send message")` looks the
// string up in the active dictionary and returns the translation; with no
// translation (or in English mode) it returns the key itself. This keeps the
// English UI byte-identical to pre-i18n behavior — tests and e2e keep
// passing unchanged — and avoids maintaining a parallel en dictionary.
//
// Add a language by dropping a dictionary file into ./zh (or a sibling
// folder) and registering it in that folder's index. Interpolation:
// t("Hello, {name}", { name }) replaces {name} in either language.
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { zhDict, type MessageKey } from "./zh";

export type { MessageKey };

export type Lang = "en" | "zh-CN";

/** The translator's contract. `MessageKey` (not string) is what makes a typo in
 * a t("...") call a compile error: every literal key must exist in a zh
 * dictionary. Dynamic keys resolved at runtime (template prompts, label maps)
 * opt out explicitly with `as MessageKey` at the call site — greppable, and the
 * Python checker covers the literal remainder. */
export type TFunction = (
  key: MessageKey,
  params?: Record<string, string | number>,
) => string;

export const LANGUAGES: { value: Lang; label: string }[] = [
  { value: "en", label: "English" },
  { value: "zh-CN", label: "中文（简体）" },
];

const STORAGE_KEY = "ow.lang";

function detectLang(): Lang {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "en" || saved === "zh-CN") return saved;
  } catch {
    /* localStorage unavailable (private mode etc.) — fall through to sniffing */
  }
  try {
    const nav = typeof navigator !== "undefined" ? navigator.language || "" : "";
    return nav.toLowerCase().startsWith("zh") ? "zh-CN" : "en";
  } catch {
    return "en";
  }
}

export function interpolate(template: string, params?: Record<string, string | number>): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (m, k: string) =>
    Object.prototype.hasOwnProperty.call(params, k) ? String(params[k]) : m,
  );
}

// Module-level live language for non-React call sites (rare; prefer useI18n).
let currentLang: Lang = detectLang();

export function getLang(): Lang {
  return currentLang;
}

export function translate(key: string, params?: Record<string, string | number>): string {
  const localized = currentLang === "zh-CN" ? zhDict[key as MessageKey] : undefined;
  return interpolate(localized ?? key, params);
}

interface I18nContextValue {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: TFunction;
}

// Default context = English identity, so a component rendered outside the
// provider (a stray test render) still works and shows English.
const I18nContext = createContext<I18nContextValue>({
  lang: "en",
  setLang: () => {},
  t: (key, params) => interpolate(key, params),
});

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLangState] = useState<Lang>(detectLang);

  useEffect(() => {
    currentLang = lang;
    try {
      localStorage.setItem(STORAGE_KEY, lang);
    } catch {
      /* best-effort persistence */
    }
    document.documentElement.lang = lang;
  }, [lang]);

  const setLang = useCallback((l: Lang) => setLangState(l), []);
  const t = useCallback<TFunction>(
    (key, params) => {
      const localized = lang === "zh-CN" ? zhDict[key] : undefined;
      return interpolate(localized ?? key, params);
    },
    [lang],
  );

  const value = useMemo(() => ({ lang, setLang, t }), [lang, setLang, t]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  return useContext(I18nContext);
}

// Convenience alias: `const { t } = useT();`
export const useT = useI18n;
