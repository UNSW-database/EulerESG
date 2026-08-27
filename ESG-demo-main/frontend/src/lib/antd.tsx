"use client";

import React, { useMemo } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import type { ThemeConfig } from "antd";
import enUS from "antd/locale/en_US";
import zhCN from "antd/locale/zh_CN";
import { useAppLang } from "@/i18n/useAppLang";

const APP_THEME: ThemeConfig = {
  token: {
    colorBgContainer: "#fff",
    borderRadiusLG: 8,
    fontFamily:
      "var(--font-inter), -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', sans-serif, 'Apple Color Emoji', 'Segoe UI Emoji', 'Segoe UI Symbol', 'Noto Color Emoji'",
  },
};

export function AntdRegistry({ children }: { children: React.ReactNode }) {
  const { lang } = useAppLang();

  const locale = useMemo(() => (lang === "zh" ? zhCN : enUS), [lang]);

  return (
    <ConfigProvider
      locale={locale}
      theme={APP_THEME}>
      <AntdApp component={false}>{children}</AntdApp>
    </ConfigProvider>
  );
}
