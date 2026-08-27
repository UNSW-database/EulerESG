import { render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import HtmlLangSync from "@/i18n/HtmlLangSync";

vi.mock("@/i18n/useAppLang", () => ({
  useAppLang: () => ({ lang: "en", setLang: vi.fn() }),
}));

describe("HtmlLangSync", () => {
  it("keeps the document language aligned outside Ant Design route groups", async () => {
    render(<HtmlLangSync />);

    await waitFor(() => expect(document.documentElement.lang).toBe("en"));
  });
});
