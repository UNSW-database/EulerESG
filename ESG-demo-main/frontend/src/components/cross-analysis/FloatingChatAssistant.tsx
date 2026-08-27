"use client";

import React, { useCallback, useEffect, useRef } from "react";
import { App as AntdApp } from "antd";
import { MessageCircle, X } from "lucide-react";
import dynamic from "next/dynamic";
import { apiService, type ChatResponse } from "@/lib/api";
import { useT } from "@/i18n/useT";
import { useDraggableFloating } from "@/hooks/useDraggableFloating";
import {
  createAssistantMessageId,
  useAssistantStore,
} from "@/store/useAssistantStore";

const loadChatInterface = () => import("@/components/pdfviewer/ChatInterface");
const ChatInterface = dynamic(loadChatInterface, {
  ssr: false,
  loading: () => (
    <div
      className="flex h-full items-center justify-center text-sm text-slate-500"
      role="status"
    >
      Loading assistant...
    </div>
  ),
});

interface FloatingChatAssistantProps {
  conversationKey?: string;
  fileId?: string;
  includeContext?: boolean;
}

const FLOATING_PANEL_MARGIN_PX = 12;
const FLOATING_PANEL_GAP_PX = 12;
const FLOATING_PANEL_MAX_WIDTH_PX = 420;
const FLOATING_PANEL_MAX_HEIGHT_PX = 620;
const FLOATING_PANEL_MOBILE_MAX_HEIGHT_PX = 520;

export default function FloatingChatAssistant({
  conversationKey = "general",
  fileId,
  includeContext = false,
}: FloatingChatAssistantProps) {
  const { t } = useT();
  const { message } = AntdApp.useApp();
  const conversation = useAssistantStore(
    (state) => state.conversations[conversationKey],
  );
  const open = useAssistantStore((state) => state.open);
  const hasOpened = useAssistantStore((state) => state.hasOpened);
  const launcherPosition = useAssistantStore((state) => state.position);
  const appendMessages = useAssistantStore((state) => state.appendMessages);
  const clearConversation = useAssistantStore(
    (state) => state.clearConversation,
  );
  const ensureConversation = useAssistantStore(
    (state) => state.ensureConversation,
  );
  const markOpened = useAssistantStore((state) => state.markOpened);
  const replaceMessage = useAssistantStore((state) => state.replaceMessage);
  const setMessages = useAssistantStore((state) => state.setMessages);
  const setOpen = useAssistantStore((state) => state.setOpen);
  const setPosition = useAssistantStore((state) => state.setPosition);
  const setSessionId = useAssistantStore((state) => state.setSessionId);
  const sessionId = conversation?.sessionId;
  const welcomeText = t("chat.welcomeMessage");
  const messages = conversation?.messages.length
    ? conversation.messages
    : [{ text: welcomeText, isUser: false }];
  const panelRef = useRef<HTMLElement>(null);
  const { draggableProps, draggableRef } =
    useDraggableFloating<HTMLButtonElement>({
      position: launcherPosition,
      onPositionChange: setPosition,
    });

  useEffect(() => {
    const welcomeMessage = { text: welcomeText, isUser: false };
    ensureConversation(conversationKey, welcomeMessage);
    setMessages(conversationKey, (currentMessages) => {
      if (
        currentMessages.length === 1
        && !currentMessages[0]?.isUser
        && !currentMessages[0]?.pending
      ) {
        return [{ ...currentMessages[0], text: welcomeText }];
      }
      return currentMessages;
    });
  }, [conversationKey, ensureConversation, setMessages, welcomeText]);

  const positionPanelAtLauncher = useCallback(() => {
    const launcher = draggableRef.current;
    const panel = panelRef.current;
    if (!launcher || !panel) return;

    const visualViewport = window.visualViewport;
    const viewportLeft = visualViewport?.offsetLeft ?? 0;
    const viewportTop = visualViewport?.offsetTop ?? 0;
    const viewportWidth = visualViewport?.width ?? window.innerWidth;
    const viewportHeight = visualViewport?.height ?? window.innerHeight;
    const viewportRight = viewportLeft + viewportWidth;
    const viewportBottom = viewportTop + viewportHeight;
    const availableWidth = Math.max(
      0,
      viewportWidth - FLOATING_PANEL_MARGIN_PX * 2,
    );
    const availableHeight = Math.max(
      0,
      viewportHeight - FLOATING_PANEL_MARGIN_PX * 2,
    );
    if (availableWidth === 0 || availableHeight === 0) return;

    const panelWidth = Math.min(
      FLOATING_PANEL_MAX_WIDTH_PX,
      availableWidth,
    );
    const compactViewport = viewportWidth < 640;
    const compactPreferredHeight = Math.min(
      FLOATING_PANEL_MOBILE_MAX_HEIGHT_PX,
      Math.max(320, viewportHeight * 0.68),
    );
    const panelHeight = Math.min(
      compactViewport
        ? compactPreferredHeight
        : FLOATING_PANEL_MAX_HEIGHT_PX,
      availableHeight,
    );
    const launcherRect = launcher.getBoundingClientRect();
    const preferredRight = launcherRect.right + FLOATING_PANEL_GAP_PX;
    const preferredLeft =
      launcherRect.left - FLOATING_PANEL_GAP_PX - panelWidth;
    const minimumLeft = viewportLeft + FLOATING_PANEL_MARGIN_PX;
    const maximumLeft = Math.max(
      minimumLeft,
      viewportRight - FLOATING_PANEL_MARGIN_PX - panelWidth,
    );
    const minimumTop = viewportTop + FLOATING_PANEL_MARGIN_PX;
    const maximumTop = Math.max(
      minimumTop,
      viewportBottom - FLOATING_PANEL_MARGIN_PX - panelHeight,
    );
    const clamp = (value: number, minimum: number, maximum: number) =>
      Math.min(maximum, Math.max(minimum, value));

    let placement: "right" | "left" | "top" | "bottom" = "right";
    let left = preferredRight;
    let top = launcherRect.bottom - panelHeight;

    if (preferredRight + panelWidth > viewportRight - FLOATING_PANEL_MARGIN_PX) {
      if (preferredLeft >= minimumLeft) {
        placement = "left";
        left = preferredLeft;
      } else {
        const centeredLeft =
          launcherRect.left + launcherRect.width / 2 - panelWidth / 2;
        left = clamp(centeredLeft, minimumLeft, maximumLeft);
        const aboveTop = launcherRect.top - FLOATING_PANEL_GAP_PX - panelHeight;
        const belowTop = launcherRect.bottom + FLOATING_PANEL_GAP_PX;
        const spaceAbove = launcherRect.top - minimumTop;
        const spaceBelow =
          viewportBottom - FLOATING_PANEL_MARGIN_PX - launcherRect.bottom;
        if (aboveTop >= minimumTop || spaceAbove >= spaceBelow) {
          placement = "top";
          top = aboveTop;
        } else {
          placement = "bottom";
          top = belowTop;
        }
      }
    }

    left = clamp(left, minimumLeft, maximumLeft);
    top = clamp(top, minimumTop, maximumTop);
    panel.dataset.placement = placement;
    panel.style.left = `${Math.round(left)}px`;
    panel.style.top = `${Math.round(top)}px`;
    panel.style.right = "auto";
    panel.style.bottom = "auto";
    panel.style.width = `${Math.round(panelWidth)}px`;
    panel.style.height = `${Math.round(panelHeight)}px`;
    panel.style.transformOrigin = {
      right: "bottom left",
      left: "bottom right",
      top: "bottom center",
      bottom: "top center",
    }[placement];
  }, [draggableRef]);

  const closeAssistant = useCallback(() => {
    setOpen(false);
    window.requestAnimationFrame(() => draggableRef.current?.focus());
  }, [draggableRef, setOpen]);

  const toggleAssistant = useCallback(() => {
    if (open) {
      closeAssistant();
      return;
    }
    positionPanelAtLauncher();
    markOpened();
    setOpen(true);
  }, [closeAssistant, markOpened, open, positionPanelAtLauncher, setOpen]);

  useEffect(() => {
    if (!open) return;

    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeAssistant();
    };
    const keepPanelAnchored = () => positionPanelAtLauncher();
    window.addEventListener("keydown", closeOnEscape);
    window.addEventListener("resize", keepPanelAnchored);
    window.addEventListener("orientationchange", keepPanelAnchored);
    window.visualViewport?.addEventListener("resize", keepPanelAnchored);
    window.visualViewport?.addEventListener("scroll", keepPanelAnchored);
    const resizeObserver = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(keepPanelAnchored);
    const launcher = draggableRef.current;
    const sidebar = launcher
      ?.closest("[data-dashboard-shell]")
      ?.querySelector("aside[data-collapsed]");
    if (launcher) resizeObserver?.observe(launcher);
    if (sidebar) resizeObserver?.observe(sidebar);
    keepPanelAnchored();

    return () => {
      window.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("resize", keepPanelAnchored);
      window.removeEventListener("orientationchange", keepPanelAnchored);
      window.visualViewport?.removeEventListener("resize", keepPanelAnchored);
      window.visualViewport?.removeEventListener("scroll", keepPanelAnchored);
      resizeObserver?.disconnect();
    };
  }, [closeAssistant, draggableRef, open, positionPanelAtLauncher]);

  const handleSendMessage = useCallback(
    async (userMessage: string) => {
      const pendingMessageId = createAssistantMessageId();
      appendMessages(conversationKey, [
        { text: userMessage, isUser: true },
        {
          id: pendingMessageId,
          pending: true,
          text: t("chat.thinking"),
          isUser: false,
        },
      ]);

      try {
        const request = {
          message: userMessage,
          include_context: fileId ? true : includeContext,
          session_id: sessionId,
        };
        const response: ChatResponse = fileId
          ? await apiService.sendMessageForFile(fileId, request)
          : await apiService.sendMessage(request);
        setSessionId(conversationKey, response.session_id);
        replaceMessage(conversationKey, pendingMessageId, {
          text: response.response,
          isUser: false,
        });
      } catch (error) {
        console.error("Chat error:", error);
        message.error(t("chat.failedToSend", { error: String(error) }));
        replaceMessage(conversationKey, pendingMessageId, {
          text: t("chat.genericError"),
          isUser: false,
        });
      }
    },
    [
      appendMessages,
      conversationKey,
      fileId,
      includeContext,
      message,
      replaceMessage,
      sessionId,
      setSessionId,
      t,
    ],
  );

  const handleClearChat = useCallback(() => {
    clearConversation(conversationKey, {
      text: welcomeText,
      isUser: false,
    });
  }, [clearConversation, conversationKey, welcomeText]);

  return (
    <>
      <button
        ref={draggableRef}
        type="button"
        {...draggableProps}
        onPointerDown={(event) => {
          void loadChatInterface().catch(() => undefined);
          draggableProps.onPointerDown(event);
        }}
        onMouseEnter={() => {
          void loadChatInterface().catch(() => undefined);
        }}
        onFocus={() => {
          void loadChatInterface().catch(() => undefined);
        }}
        onPointerMove={(event) => {
          draggableProps.onPointerMove(event);
          if (open) positionPanelAtLauncher();
        }}
        onPointerUp={(event) => {
          draggableProps.onPointerUp(event);
          if (open) positionPanelAtLauncher();
        }}
        onClick={toggleAssistant}
        className={`dashboard-chat-launcher draggable-assistant-launcher fixed z-[51] flex items-center gap-2 rounded-full px-4 py-3 text-white shadow-lg transition-[transform,background-color,box-shadow] hover:bg-slate-600 focus:outline-none focus:ring-2 focus:ring-slate-500 focus:ring-offset-2 ${
          open ? "bg-slate-800" : "bg-slate-700"
        }`}
        aria-label={open ? t("common.close") : "AI Assistant"}
        aria-controls="floating-ai-assistant"
        aria-expanded={open}
        aria-haspopup="dialog"
        title={t("chat.dragAssistantHint")}
      >
        {open ? (
          <X className="h-5 w-5" />
        ) : (
          <MessageCircle className="h-5 w-5" />
        )}
        <span className="dashboard-chat-launcher-label text-sm font-medium">
          {open ? t("common.close") : "AI Assistant"}
        </span>
      </button>

      <section
        ref={panelRef}
        id="floating-ai-assistant"
        role="dialog"
        aria-modal="false"
        aria-label={t("chat.aiAssistant")}
        aria-hidden={!open}
        data-testid="floating-ai-assistant"
        className={`dashboard-chat-panel fixed z-50 flex min-h-0 flex-col overflow-hidden rounded-2xl border border-slate-200/90 bg-white shadow-[0_24px_80px_rgba(15,23,42,0.22)] transition-[opacity,transform,visibility] duration-300 ease-[var(--motion-fluid)] ${
          open
            ? "visible translate-y-0 scale-100 opacity-100"
            : "invisible pointer-events-none translate-y-3 scale-[0.98] opacity-0"
        }`}
        style={{
          left: FLOATING_PANEL_MARGIN_PX,
          top: FLOATING_PANEL_MARGIN_PX,
          right: "auto",
          bottom: "auto",
          width: FLOATING_PANEL_MAX_WIDTH_PX,
          height: FLOATING_PANEL_MAX_HEIGHT_PX,
        }}
      >
        <div className="flex h-full flex-col min-h-0">
          {hasOpened ? (
            <ChatInterface
              messages={messages}
              onSendMessage={handleSendMessage}
              onClearChat={handleClearChat}
              onClose={closeAssistant}
              onReferenceClick={() => {}}
            />
          ) : null}
        </div>
      </section>
    </>
  );
}
