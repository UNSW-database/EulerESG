import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import FloatingChatAssistant from "@/components/cross-analysis/FloatingChatAssistant";
import { useAssistantStore } from "@/store/useAssistantStore";

const apiMocks = vi.hoisted(() => ({
  sendMessage: vi.fn(),
  sendMessageForFile: vi.fn(),
}));

const chatInterfaceMocks = vi.hoisted(() => ({
  moduleLoads: 0,
}));

vi.mock("antd", () => ({
  App: {
    useApp: () => ({ message: { error: vi.fn() } }),
  },
}));

vi.mock("@/components/pdfviewer/ChatInterface", async () => {
  chatInterfaceMocks.moduleLoads += 1;
  const { useState } = await vi.importActual<typeof import("react")>("react");

  function MockChatInterface({
    messages,
    onClearChat,
    onClose,
    onSendMessage,
  }: {
    messages: Array<{ isUser: boolean; text: string }>;
    onClearChat?: () => void;
    onClose?: () => void;
    onSendMessage: (message: string) => Promise<void>;
  }) {
    const [draft, setDraft] = useState("");

    return (
      <div data-testid="mock-chat-interface">
        <div data-testid="assistant-messages">
          {messages.map((message, index) => (
            <span data-is-user={String(message.isUser)} key={`${message.text}-${index}`}>
              {message.text}
            </span>
          ))}
        </div>
        <label>
          Assistant draft
          <input
            aria-label="Assistant draft"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
        </label>
        <button type="button" onClick={() => void onSendMessage("test question")}>
          Send test question
        </button>
        <button type="button" onClick={onClose}>
          Close test assistant
        </button>
        <button type="button" onClick={onClearChat}>
          Clear test assistant
        </button>
      </div>
    );
  }

  return { default: MockChatInterface };
});

vi.mock("@/lib/api", () => ({
  apiService: {
    sendMessage: apiMocks.sendMessage,
    sendMessageForFile: apiMocks.sendMessageForFile,
  },
}));

vi.mock("@/i18n/useT", () => ({
  useT: () => ({ t: (key: string) => key }),
}));

type MockPointerEventInit = MouseEventInit & {
  isPrimary?: boolean;
  pointerId?: number;
  pointerType?: string;
};

const dispatchPointerEvent = (
  target: Node,
  type: string,
  init: MockPointerEventInit,
) => {
  const event = new MouseEvent(type, {
    bubbles: true,
    cancelable: true,
    ...init,
  });
  Object.defineProperties(event, {
    isPrimary: { configurable: true, value: init.isPrimary ?? true },
    pointerId: { configurable: true, value: init.pointerId ?? 1 },
    pointerType: { configurable: true, value: init.pointerType ?? "mouse" },
  });
  fireEvent(target, event);
};

describe("FloatingChatAssistant", () => {
  beforeEach(() => {
    useAssistantStore.getState().resetAll();
    window.sessionStorage.clear();
    apiMocks.sendMessage.mockReset();
    apiMocks.sendMessage.mockResolvedValue({
      session_id: "assistant-session",
      response: "answer",
    });
    apiMocks.sendMessageForFile.mockReset();
    apiMocks.sendMessageForFile.mockResolvedValue({
      session_id: "report-assistant-session",
      response: "report answer",
    });
  });

  it("does not load or mount the chat interface while initially closed", () => {
    expect(chatInterfaceMocks.moduleLoads).toBe(0);

    render(<FloatingChatAssistant />);

    expect(chatInterfaceMocks.moduleLoads).toBe(0);
    expect(screen.queryByTestId("mock-chat-interface")).not.toBeInTheDocument();
  });

  it("loads and mounts the chat interface on the first open", async () => {
    expect(chatInterfaceMocks.moduleLoads).toBe(0);

    render(<FloatingChatAssistant />);

    fireEvent.click(screen.getByRole("button", { name: "AI Assistant" }));

    await waitFor(() => expect(chatInterfaceMocks.moduleLoads).toBe(1));
    expect(await screen.findByTestId("mock-chat-interface")).toBeInTheDocument();
  });

  it("uses the shared floating lower-left launcher with the requested label", () => {
    render(<FloatingChatAssistant />);

    const launcher = screen.getByRole("button", { name: "AI Assistant" });
    const panel = screen.getByTestId("floating-ai-assistant");
    expect(launcher).toHaveTextContent("AI Assistant");
    expect(launcher).toHaveClass(
      "dashboard-chat-launcher",
      "draggable-assistant-launcher",
      "fixed",
    );
    expect(launcher).toHaveAttribute("data-draggable-assistant", "true");
    expect(launcher).toHaveAttribute("aria-controls", "floating-ai-assistant");
    expect(launcher).toHaveAttribute("aria-expanded", "false");
    expect(launcher).not.toHaveClass("right-6", "bottom-20");
    expect(panel).toHaveAttribute("aria-hidden", "true");
    expect(panel).toHaveAttribute("aria-modal", "false");
    expect(panel).toHaveClass(
      "dashboard-chat-panel",
      "fixed",
      "invisible",
      "pointer-events-none",
    );
  });

  it("opens and closes a non-modal floating panel without a drawer or page mask", async () => {
    render(<FloatingChatAssistant />);

    const launcher = screen.getByRole("button", { name: "AI Assistant" });
    const panel = screen.getByTestId("floating-ai-assistant");

    expect(document.querySelector(".ant-drawer")).not.toBeInTheDocument();
    expect(document.querySelector(".ant-drawer-mask")).not.toBeInTheDocument();
    expect(
      document.querySelector('[data-slot="sheet-overlay"]'),
    ).not.toBeInTheDocument();

    fireEvent.click(launcher);
    expect(launcher).toHaveAttribute("aria-expanded", "true");
    expect(panel).toHaveAttribute("aria-hidden", "false");
    expect(panel).toHaveAttribute("aria-modal", "false");
    expect(panel).toHaveClass("visible", "opacity-100");
    expect(panel).not.toHaveClass("invisible", "pointer-events-none");
    expect(screen.getByRole("dialog", { name: "chat.aiAssistant" })).toBe(panel);

    fireEvent.click(launcher);
    expect(launcher).toHaveAttribute("aria-expanded", "false");
    expect(panel).toHaveAttribute("aria-hidden", "true");
    expect(panel).toHaveClass("invisible", "pointer-events-none", "opacity-0");

    fireEvent.click(launcher);
    fireEvent.click(
      await screen.findByRole("button", { name: "Close test assistant" }),
    );
    expect(panel).toHaveAttribute("aria-hidden", "true");

    fireEvent.click(launcher);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(launcher).toHaveAttribute("aria-expanded", "false");
    expect(panel).toHaveAttribute("aria-hidden", "true");
  });

  it("keeps the mounted chat and its draft when closed and reopened", async () => {
    render(<FloatingChatAssistant />);

    const launcher = screen.getByRole("button", { name: "AI Assistant" });
    fireEvent.click(launcher);
    const draftInput = await screen.findByRole("textbox", {
      name: "Assistant draft",
    });
    fireEvent.change(draftInput, { target: { value: "keep this draft" } });

    const loadCountAfterFirstOpen = chatInterfaceMocks.moduleLoads;
    fireEvent.click(
      screen.getByRole("button", { name: "Close test assistant" }),
    );
    expect(screen.getByTestId("floating-ai-assistant")).toHaveAttribute(
      "aria-hidden",
      "true",
    );
    expect(draftInput).toHaveValue("keep this draft");

    fireEvent.click(screen.getByRole("button", { name: "AI Assistant" }));
    const reopenedDraftInput = screen.getByRole("textbox", {
      name: "Assistant draft",
    });
    expect(reopenedDraftInput).toBe(draftInput);
    expect(reopenedDraftInput).toHaveValue("keep this draft");
    expect(chatInterfaceMocks.moduleLoads).toBe(loadCountAfterFirstOpen);
  });

  it("uses the launcher edge instead of the expanded sidebar edge as its anchor", () => {
    vi.spyOn(window, "innerWidth", "get").mockReturnValue(1000);
    vi.spyOn(window, "innerHeight", "get").mockReturnValue(800);
    const { container } = render(
      <div data-dashboard-shell>
        <aside data-collapsed="false" />
        <FloatingChatAssistant />
      </div>,
    );

    const launcher = screen.getByRole("button", { name: "AI Assistant" });
    const panel = screen.getByTestId("floating-ai-assistant");
    const sidebar = container.querySelector("aside") as HTMLElement;
    vi.spyOn(sidebar, "getBoundingClientRect").mockReturnValue({
      bottom: 800,
      height: 800,
      left: 0,
      right: 260,
      top: 0,
      width: 260,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    });
    vi.spyOn(launcher, "getBoundingClientRect").mockReturnValue({
      bottom: 720,
      height: 48,
      left: 24,
      right: 184,
      top: 672,
      width: 160,
      x: 24,
      y: 672,
      toJSON: () => ({}),
    });

    fireEvent.click(launcher);

    expect(panel).toHaveAttribute("data-placement", "right");
    expect(panel).toHaveStyle({ left: "196px", width: "420px" });
    expect(Number.parseFloat(panel.style.left) - 184).toBe(12);
  });

  it("anchors the compact panel to the launcher and clamps it inside the viewport", () => {
    vi.spyOn(window, "innerWidth", "get").mockReturnValue(360);
    vi.spyOn(window, "innerHeight", "get").mockReturnValue(640);
    render(<FloatingChatAssistant />);

    const launcher = screen.getByRole("button", { name: "AI Assistant" });
    const panel = screen.getByTestId("floating-ai-assistant");
    vi.spyOn(launcher, "getBoundingClientRect").mockReturnValue({
      bottom: 610,
      height: 40,
      left: 310,
      right: 350,
      top: 570,
      width: 40,
      x: 310,
      y: 570,
      toJSON: () => ({}),
    });

    fireEvent.click(launcher);

    expect(panel).toHaveAttribute("data-placement", "top");
    expect(panel).toHaveStyle({
      bottom: "auto",
      height: "435px",
      left: "12px",
      right: "auto",
      top: "123px",
      transformOrigin: "bottom center",
      width: "336px",
    });

    const left = Number.parseFloat(panel.style.left);
    const top = Number.parseFloat(panel.style.top);
    const width = Number.parseFloat(panel.style.width);
    const height = Number.parseFloat(panel.style.height);
    expect(left).toBeGreaterThanOrEqual(12);
    expect(top).toBeGreaterThanOrEqual(12);
    expect(left + width).toBeLessThanOrEqual(360 - 12);
    expect(top + height).toBeLessThanOrEqual(640 - 12);
  });

  it("drags within the viewport without treating the release as an open click", () => {
    const view = render(<FloatingChatAssistant />);

    vi.spyOn(window, "innerWidth", "get").mockReturnValue(800);
    vi.spyOn(window, "innerHeight", "get").mockReturnValue(600);

    const launcher = screen.getByRole("button", { name: "AI Assistant" });
    const panel = screen.getByTestId("floating-ai-assistant");
    vi.spyOn(launcher, "getBoundingClientRect").mockImplementation(() => {
      const left = Number.parseFloat(launcher.style.left) || 24;
      const top = Number.parseFloat(launcher.style.top) || 500;
      return {
        bottom: top + 48,
        height: 48,
        left,
        right: left + 160,
        top,
        width: 160,
        x: left,
        y: top,
        toJSON: () => ({}),
      };
    });

    fireEvent.click(launcher);
    expect(panel).toHaveAttribute("aria-hidden", "false");

    dispatchPointerEvent(launcher, "pointerdown", {
      button: 0,
      clientX: 50,
      clientY: 520,
      pointerId: 9,
    });
    dispatchPointerEvent(launcher, "pointermove", {
      button: 0,
      clientX: 760,
      clientY: -100,
      pointerId: 9,
    });
    dispatchPointerEvent(launcher, "pointerup", {
      button: 0,
      clientX: 760,
      clientY: -100,
      pointerId: 9,
    });

    expect(launcher).toHaveStyle({
      bottom: "auto",
      left: "632px",
      right: "auto",
      top: "8px",
    });
    expect(launcher).toHaveAttribute("data-dragging", "false");
    expect(panel).toHaveAttribute("data-placement", "left");
    expect(panel).toHaveStyle({ left: "200px", width: "420px" });
    expect(
      Number.parseFloat(launcher.style.left) -
        (Number.parseFloat(panel.style.left) + Number.parseFloat(panel.style.width)),
    ).toBe(12);

    fireEvent.click(launcher, { detail: 1 });
    expect(panel).toHaveAttribute("aria-hidden", "false");

    fireEvent.click(launcher);
    expect(panel).toHaveAttribute("aria-hidden", "true");
    expect(launcher).toHaveStyle({ left: "632px", top: "8px" });

    view.unmount();
    const remountRect = vi
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockReturnValue({
        bottom: 56,
        height: 48,
        left: 632,
        right: 792,
        top: 8,
        width: 160,
        x: 632,
        y: 8,
        toJSON: () => ({}),
      });
    render(<FloatingChatAssistant />);

    expect(screen.getByRole("button", { name: "AI Assistant" })).toHaveStyle({
      bottom: "auto",
      left: "632px",
      right: "auto",
      top: "8px",
    });
    remountRect.mockRestore();
  });

  it("uses generic mode on the homepage and preserves the server chat session", async () => {
    render(<FloatingChatAssistant includeContext={false} />);

    fireEvent.click(screen.getByRole("button", { name: "AI Assistant" }));
    const send = await screen.findByRole("button", {
      name: "Send test question",
    });
    fireEvent.click(send);
    await waitFor(() => expect(apiMocks.sendMessage).toHaveBeenCalledTimes(1));
    expect(apiMocks.sendMessage).toHaveBeenNthCalledWith(1, {
      message: "test question",
      include_context: false,
      session_id: undefined,
    });

    fireEvent.click(send);
    await waitFor(() => expect(apiMocks.sendMessage).toHaveBeenCalledTimes(2));
    expect(apiMocks.sendMessage).toHaveBeenNthCalledWith(2, {
      message: "test question",
      include_context: false,
      session_id: "assistant-session",
    });
  });

  it("keeps the open general conversation and pending response across a route remount", async () => {
    let resolveFirstResponse: ((response: {
      response: string;
      session_id: string;
    }) => void) | undefined;
    apiMocks.sendMessage.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveFirstResponse = resolve;
      }),
    );

    const firstPage = render(
      <FloatingChatAssistant
        conversationKey="general"
        includeContext={false}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "AI Assistant" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Send test question" }),
    );

    await waitFor(() => expect(apiMocks.sendMessage).toHaveBeenCalledTimes(1));
    expect(apiMocks.sendMessage).toHaveBeenNthCalledWith(1, {
      message: "test question",
      include_context: false,
      session_id: undefined,
    });
    expect(screen.getByTestId("assistant-messages")).toHaveTextContent(
      "test question",
    );

    firstPage.unmount();
    render(
      <FloatingChatAssistant
        conversationKey="general"
        includeContext={false}
      />,
    );

    expect(screen.getByRole("button", { name: "common.close" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(screen.getByTestId("floating-ai-assistant")).toHaveAttribute(
      "aria-hidden",
      "false",
    );
    expect(await screen.findByTestId("assistant-messages")).toHaveTextContent(
      "test question",
    );

    await act(async () => {
      resolveFirstResponse?.({
        response: "answer after navigation",
        session_id: "general-session-after-navigation",
      });
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(screen.getByTestId("assistant-messages")).toHaveTextContent(
        "answer after navigation",
      );
    });

    fireEvent.click(screen.getByRole("button", { name: "Send test question" }));
    await waitFor(() => expect(apiMocks.sendMessage).toHaveBeenCalledTimes(2));
    expect(apiMocks.sendMessage).toHaveBeenNthCalledWith(2, {
      message: "test question",
      include_context: false,
      session_id: "general-session-after-navigation",
    });
  });

  it("isolates report conversations and uses the report-scoped chat endpoint", async () => {
    const reportA = render(
      <FloatingChatAssistant
        conversationKey="file:report-a"
        fileId="report-a"
        includeContext
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "AI Assistant" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Send test question" }),
    );

    await waitFor(() => {
      expect(apiMocks.sendMessageForFile).toHaveBeenCalledWith("report-a", {
        message: "test question",
        include_context: true,
        session_id: undefined,
      });
      expect(screen.getByTestId("assistant-messages")).toHaveTextContent(
        "report answer",
      );
    });
    expect(apiMocks.sendMessage).not.toHaveBeenCalled();

    reportA.unmount();
    const reportB = render(
      <FloatingChatAssistant
        conversationKey="file:report-b"
        fileId="report-b"
        includeContext
      />,
    );

    expect(screen.getByRole("button", { name: "common.close" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(await screen.findByTestId("assistant-messages")).not.toHaveTextContent(
      "report answer",
    );
    fireEvent.click(screen.getByRole("button", { name: "Send test question" }));
    await waitFor(() => {
      expect(apiMocks.sendMessageForFile).toHaveBeenNthCalledWith(2, "report-b", {
        message: "test question",
        include_context: true,
        session_id: undefined,
      });
    });

    reportB.unmount();
    render(
      <FloatingChatAssistant
        conversationKey="file:report-a"
        fileId="report-a"
        includeContext
      />,
    );
    expect(await screen.findByTestId("assistant-messages")).toHaveTextContent(
      "report answer",
    );

    fireEvent.click(screen.getByRole("button", { name: "Send test question" }));
    await waitFor(() => {
      expect(apiMocks.sendMessageForFile).toHaveBeenNthCalledWith(3, "report-a", {
        message: "test question",
        include_context: true,
        session_id: "report-assistant-session",
      });
    });
  });
});
