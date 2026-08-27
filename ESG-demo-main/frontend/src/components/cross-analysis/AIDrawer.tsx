"use client";

import React, { useEffect, useMemo, useState } from "react";
import { App as AntdApp, Input } from "antd";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Sparkles, X } from "lucide-react";
import { apiService, ChatResponse } from "@/lib/api";
import { useT } from "@/i18n/useT";

type Msg = { role: "user" | "assistant"; content: string; ts: number };

function stableHash(s: string) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = (h * 16777619) >>> 0;
  }
  return h.toString(16);
}

export default function AIDrawer({
  ids,
  dimension,
  topic,
}: {
  ids: string[];
  dimension: string;
  topic: string;
}) {
  const { t } = useT();
  const { message } = AntdApp.useApp();

  const scopeKey = useMemo(() => stableHash(ids.join(",")), [ids]);
  const storageKey = useMemo(() => `cross_chat_${scopeKey}`, [scopeKey]);
  const sessionKey = useMemo(() => `cross_chat_session_${scopeKey}`, [scopeKey]);

  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      if (raw) setMessages(JSON.parse(raw));
      const sid = localStorage.getItem(sessionKey);
      if (sid) setSessionId(sid);
    } catch {
      // ignore
    }
  }, [storageKey, sessionKey]);

  useEffect(() => {
    try {
      localStorage.setItem(storageKey, JSON.stringify(messages.slice(-50)));
    } catch {
      // ignore
    }
  }, [messages, storageKey]);

  const send = async () => {
    const text = input.trim();
    if (!text) return;
    if (ids.length < 2) {
      message.warning(t("files.selectAtLeastTwoReports"));
      return;
    }

    setInput("");
    const nextUser: Msg = { role: "user", content: text, ts: Date.now() };
    setMessages((prev) => [...prev, nextUser]);

    try {
      setSending(true);
      const res: ChatResponse = await apiService.sendMessage({
        session_id: sessionId || undefined,
        message: text,
        include_context: true,
        context: {
          ids: ids.join(","),
          dimension,
          topic,
        },
      });
      if (!sessionId && res.session_id) {
        setSessionId(res.session_id);
        try {
          localStorage.setItem(sessionKey, res.session_id);
        } catch {
          // ignore
        }
      }

      const nextAI: Msg = { role: "assistant", content: res.response, ts: Date.now() };
      setMessages((prev) => [...prev, nextAI]);
    } catch (e: any) {
      message.error(e?.message || t("crossAnalysis.ai.requestFailed"));
      const nextAI: Msg = {
        role: "assistant",
        content:
          t("crossAnalysis.ai.requestFailedFallback"),
        ts: Date.now(),
      };
      setMessages((prev) => [...prev, nextAI]);
    } finally {
      setSending(false);
    }
  };

  const clear = () => {
    setMessages([]);
    setSessionId(null);
    try {
      localStorage.removeItem(storageKey);
      localStorage.removeItem(sessionKey);
    } catch {
      // ignore
    }
  };

  return (
    <div className="fixed bottom-6 right-6 z-50">
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetTrigger asChild>
          <button className="group flex items-center gap-2 rounded-full border border-slate-200 bg-white/70 px-4 py-3 text-sm shadow-lg backdrop-blur transition hover:bg-white">
            <Sparkles size={18} className="text-slate-600" />
            <span className="font-medium text-slate-800">{t("crossAnalysis.ai.ask")}</span>
            <span className="hidden sm:inline text-xs text-slate-500">{t("crossAnalysis.ai.evidenceAware")}</span>
          </button>
        </SheetTrigger>
        <SheetContent side="right" className="w-[420px] overflow-y-auto overscroll-y-contain sm:w-[480px]">
          <SheetHeader>
            <div className="flex items-center justify-between gap-3">
              <SheetTitle className="text-base">{t("crossAnalysis.ai.assistant")}</SheetTitle>
              <div className="flex items-center gap-2">
                <Button variant="ghost" size="sm" onClick={clear}>
                  {t("common.clear")}
                </Button>
                <Button variant="ghost" size="icon" onClick={() => setOpen(false)}>
                  <X size={16} />
                </Button>
              </div>
            </div>
            <div className="mt-1 text-xs text-slate-500">
              {t("crossAnalysis.ai.retrievalHint")}
            </div>
          </SheetHeader>

          <div className="mt-4 flex h-[65vh] flex-col rounded-2xl border border-slate-200 bg-white/60 p-3 shadow-sm backdrop-blur">
            <div
              className="flex-1 space-y-3 overflow-auto overscroll-y-contain pr-1"
              style={{ WebkitOverflowScrolling: "touch" }}
            >
              {messages.length === 0 ? (
                <div className="rounded-xl border border-slate-200 bg-white/70 p-3 text-sm text-slate-700">
                  <div className="font-medium">{t("crossAnalysis.ai.suggestedPrompts")}</div>
                  <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-slate-600">
                    <li>{t("crossAnalysis.ai.prompt1")}</li>
                    <li>{t("crossAnalysis.ai.prompt2")}</li>
                    <li>{t("crossAnalysis.ai.prompt3")}</li>
                  </ul>
                </div>
              ) : null}

              {messages.map((m, i) => (
                <div
                  key={i}
                  className={
                    "max-w-[92%] rounded-2xl px-3 py-2 text-sm leading-relaxed " +
                    (m.role === "user"
                      ? "ml-auto bg-slate-900 text-white"
                      : "mr-auto bg-white text-slate-800 border border-slate-200")
                  }
                >
                  {m.content}
                </div>
              ))}
            </div>

            <div className="mt-3 flex gap-2">
              <Input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onPressEnter={send}
                placeholder={t("crossAnalysis.ai.placeholder")}
                disabled={sending}
              />
              <Button onClick={send} disabled={sending || !input.trim()}>
                {t("crossAnalysis.ai.send")}
              </Button>
            </div>
            <div className="mt-2 text-[11px] text-slate-500">
              {t("crossAnalysis.ai.context")}: {dimension} · {topic} · {ids.length} {t("crossAnalysis.ai.reports")}
            </div>
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
