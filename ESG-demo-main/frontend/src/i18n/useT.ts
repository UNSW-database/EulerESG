"use client";

import { useCallback, useMemo } from "react";
import { DICT } from "./dict";
import { useAppLang } from "./useAppLang";
import type { Lang } from "./dict";

function getByPath(obj: any, path: string): unknown {
  return path.split(".").reduce((acc, key) => (acc != null ? acc[key] : undefined), obj);
}

function interpolate(template: string, vars?: Record<string, any>) {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (_, k) => {
    const v = vars[k];
    return v === undefined || v === null ? "" : String(v);
  });
}

export function tWithLang(lang: Lang, key: string, vars?: Record<string, any>) {
  const dict = DICT[lang];
  const raw = getByPath(dict, key);
  if (typeof raw === "string") return interpolate(raw, vars);
  // fallback to zh then key
  const zhRaw = getByPath(DICT.zh, key);
  if (typeof zhRaw === "string") return interpolate(zhRaw, vars);
  return key;
}

export function useT() {
  const { lang } = useAppLang();

  // IMPORTANT: memoize `t` so its identity stays stable across renders.
  // If `t` changes every render, any useCallback/useEffect that depends on it
  // can create an infinite re-fetch / re-render loop (e.g., Cross Analysis).
  const t = useCallback(
    (key: string, vars?: Record<string, any>) => tWithLang(lang, key, vars),
    [lang]
  );

  return useMemo(() => ({ lang, t }), [lang, t]);
}
