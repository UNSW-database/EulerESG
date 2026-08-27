import type { PropsWithChildren } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { AntdRegistry } from "@/lib/antd";

vi.mock("antd", async () => {
  const React = await import("react");
  return {
    App: ({ children }: PropsWithChildren) =>
      React.createElement(React.Fragment, null, children),
    ConfigProvider: ({ children }: PropsWithChildren) =>
      React.createElement(React.Fragment, null, children),
  };
});

vi.mock("antd/locale/en_US", () => ({ default: { locale: "en" } }));
vi.mock("antd/locale/zh_CN", () => ({ default: { locale: "zh-cn" } }));

vi.mock("@/i18n/useAppLang", () => ({
  useAppLang: () => ({ lang: "zh", setLang: vi.fn() }),
}));

describe("AntdRegistry", () => {
  it("includes application content in the initial server render", () => {
    const html = renderToStaticMarkup(
      <AntdRegistry>
        <main>Standards Library content</main>
      </AntdRegistry>,
    );

    expect(html).toContain("<main>Standards Library content</main>");
    expect(html).not.toContain("min-height:100vh");
  });
});
