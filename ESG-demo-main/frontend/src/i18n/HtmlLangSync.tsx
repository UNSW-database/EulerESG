"use client";

import { useEffect } from "react";

import { useAppLang } from "./useAppLang";

export default function HtmlLangSync() {
  const { lang } = useAppLang();

  useEffect(() => {
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  }, [lang]);

  return null;
}
