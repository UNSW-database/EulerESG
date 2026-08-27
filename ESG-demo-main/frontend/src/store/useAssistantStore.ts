import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

export const ASSISTANT_STORAGE_KEY = "euler-ai-assistant-state";

export interface AssistantMessage {
  id?: string;
  isUser: boolean;
  pending?: boolean;
  text: string;
}

export interface AssistantConversation {
  messages: AssistantMessage[];
  sessionId?: string;
}

export interface AssistantPosition {
  x: number;
  y: number;
}

type MessagesUpdater =
  | AssistantMessage[]
  | ((messages: AssistantMessage[]) => AssistantMessage[]);

interface AssistantStore {
  conversations: Record<string, AssistantConversation>;
  hasOpened: boolean;
  open: boolean;
  position: AssistantPosition | null;
  appendMessages: (key: string, messages: AssistantMessage[]) => void;
  clearConversation: (key: string, welcomeMessage: AssistantMessage) => void;
  ensureConversation: (key: string, welcomeMessage: AssistantMessage) => void;
  markOpened: () => void;
  replaceMessage: (
    key: string,
    messageId: string,
    replacement: AssistantMessage,
  ) => void;
  resetAll: () => void;
  setMessages: (key: string, updater: MessagesUpdater) => void;
  setOpen: (open: boolean) => void;
  setPosition: (position: AssistantPosition | null) => void;
  setSessionId: (key: string, sessionId?: string) => void;
  toggleOpen: () => void;
}

const initialState = {
  conversations: {},
  hasOpened: false,
  open: false,
  position: null,
};

const emptyConversation = (): AssistantConversation => ({ messages: [] });

export const useAssistantStore = create<AssistantStore>()(
  persist(
    (set) => ({
      ...initialState,
      appendMessages: (key, messages) =>
        set((state) => {
          const conversation = state.conversations[key] ?? emptyConversation();
          return {
            conversations: {
              ...state.conversations,
              [key]: {
                ...conversation,
                messages: [...conversation.messages, ...messages],
              },
            },
          };
        }),
      clearConversation: (key, welcomeMessage) =>
        set((state) => ({
          conversations: {
            ...state.conversations,
            [key]: { messages: [welcomeMessage] },
          },
        })),
      ensureConversation: (key, welcomeMessage) =>
        set((state) => {
          if (state.conversations[key]) return state;
          return {
            conversations: {
              ...state.conversations,
              [key]: { messages: [welcomeMessage] },
            },
          };
        }),
      markOpened: () => set({ hasOpened: true }),
      replaceMessage: (key, messageId, replacement) =>
        set((state) => {
          const conversation = state.conversations[key];
          if (!conversation) return state;
          return {
            conversations: {
              ...state.conversations,
              [key]: {
                ...conversation,
                messages: conversation.messages.map((message) =>
                  message.id === messageId ? replacement : message,
                ),
              },
            },
          };
        }),
      resetAll: () => set(initialState),
      setMessages: (key, updater) =>
        set((state) => {
          const conversation = state.conversations[key] ?? emptyConversation();
          const messages = typeof updater === "function"
            ? updater(conversation.messages)
            : updater;
          return {
            conversations: {
              ...state.conversations,
              [key]: { ...conversation, messages },
            },
          };
        }),
      setOpen: (open) => set({ open }),
      setPosition: (position) => set({ position }),
      setSessionId: (key, sessionId) =>
        set((state) => {
          const conversation = state.conversations[key] ?? emptyConversation();
          return {
            conversations: {
              ...state.conversations,
              [key]: { ...conversation, sessionId },
            },
          };
        }),
      toggleOpen: () => set((state) => ({ open: !state.open })),
    }),
    {
      name: ASSISTANT_STORAGE_KEY,
      partialize: (state) => ({
        conversations: Object.fromEntries(
          Object.entries(state.conversations).map(([key, conversation]) => [
            key,
            {
              ...conversation,
              messages: conversation.messages
                .filter((message) => !message.pending)
                .slice(-50),
            },
          ]),
        ),
        hasOpened: state.hasOpened,
        open: state.open,
        position: state.position,
      }),
      storage: createJSONStorage(() => sessionStorage),
      version: 1,
    },
  ),
);

export function createAssistantMessageId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
