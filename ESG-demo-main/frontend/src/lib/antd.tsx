"use client";

import { createCache, StyleProvider } from "@ant-design/cssinjs";
import { ConfigProvider } from "antd";

const styleCache = createCache();

export function AntdRegistry({ children }: { children: React.ReactNode }) {
  return (
    <StyleProvider cache={styleCache}>
      <ConfigProvider
        theme={{
          token: {
            colorBgContainer: "#fff",
            borderRadiusLG: 8,
            fontFamily:
              "var(--font-inter), -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', sans-serif, 'Apple Color Emoji', 'Segoe UI Emoji', 'Segoe UI Symbol', 'Noto Color Emoji'",
          },
        }}>
        {children}
      </ConfigProvider>
    </StyleProvider>
  );
}
