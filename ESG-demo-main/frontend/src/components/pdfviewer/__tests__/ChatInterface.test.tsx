import React from "react";

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ChatInterface from "../ChatInterface";

vi.mock("@/i18n/useT", () => ({
  useT: () => ({ t: (key: string) => key }),
}));

describe("ChatInterface scrolling", () => {
  it("keeps ordinary wheel native and configures message boundary chaining", () => {
    render(
      <ChatInterface
        messages={[{ text: "A previous answer", isUser: false }]}
        onReferenceClick={vi.fn()}
        onSendMessage={vi.fn()}
      />,
    );

    const messageScroller = screen.getByTestId("compliance-message-scroll");
    const wheelEvent = new WheelEvent("wheel", {
      bubbles: true,
      cancelable: true,
      deltaY: 120,
    });
    messageScroller.dispatchEvent(wheelEvent);

    expect(wheelEvent.defaultPrevented).toBe(false);
    expect(messageScroller).toHaveClass("overflow-y-auto", "overscroll-y-auto");
    expect(messageScroller.style.overscrollBehaviorY).toBe("auto");

    const composer = screen.getByPlaceholderText("chat.placeholder");
    expect(composer).toHaveClass("overscroll-y-auto");
    expect(composer.style.overscrollBehaviorY).toBe("auto");
  });
});
