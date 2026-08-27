import type { Lang } from "./dict";

export type { Lang };

const STORAGE_KEY = "app_lang";

// IMPORTANT: default to zh for SSR/CSR consistency (avoid hydration mismatch)
let currentLang: Lang = "zh";
let initialized = false;

const listeners = new Set<() => void>();

function readStorageLang(): Lang | null {
  try {
    const v = window.localStorage.getItem(STORAGE_KEY);
    return v === "zh" || v === "en" ? v : null;
  } catch {
    return null;
  }
}

function writeStorageLang(v: Lang) {
  try {
    window.localStorage.setItem(STORAGE_KEY, v);
  } catch {
    // ignore
  }
}

function notify() {
  for (const cb of listeners) cb();
}

/**
 * Initialize language from localStorage after the app mounts.
 * This avoids SSR/CSR hydration mismatch.
 */
export function initLang() {
  if (initialized) return;
  if (typeof window === "undefined") return;
  initialized = true;

  const stored = readStorageLang();
  if (stored && stored !== currentLang) {
    currentLang = stored;
    notify();
  } else if (!stored) {
    // persist default (zh)
    writeStorageLang(currentLang);
  }

  // Backward compatible with legacy dispatchers
  window.addEventListener("app_lang_change", () => {
    const v = readStorageLang();
    if (v && v !== currentLang) {
      currentLang = v;
      notify();
    }
  });
}

export function getLang(): Lang {
  return currentLang;
}

export function setLang(v: Lang) {
  if (typeof window !== "undefined") initLang();
  if (v === currentLang) return;
  currentLang = v;

  if (typeof window !== "undefined") {
    writeStorageLang(v);
    try {
      window.dispatchEvent(new Event("app_lang_change"));
    } catch {
      // ignore
    }
  }
  notify();
}

export function subscribeLang(cb: () => void) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}
