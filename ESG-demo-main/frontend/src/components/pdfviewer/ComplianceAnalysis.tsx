"use client";

import React from "react";
import { Alert, App, Button, Card, Spin } from "antd";
import { FileTextOutlined } from "@ant-design/icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { apiService } from "@/lib/api";
import ChatInterface from "./ChatInterface";
import type { File } from "@/store/useFileStore";
import { useT } from "@/i18n/useT";

interface ComplianceAnalysisProps {
  analysisFile?: File;
  onAnalysisComplete?: (result: { report_file: string; content: string }) => void;
}

type ChatMessage = { text: string; isUser: boolean };

const ComplianceAnalysis: React.FC<ComplianceAnalysisProps> = ({ analysisFile, onAnalysisComplete }) => {
  const { t, lang } = useT();
  const { message } = App.useApp();

  const [loading, setLoading] = React.useState(false);
  const [markdownContent, setMarkdownContent] = React.useState<string | null>(null);
  const [reportFile, setReportFile] = React.useState<string | null>(null);

  const [chatMessages, setChatMessages] = React.useState<ChatMessage[]>(() => [
    { text: t("compliance.chatbotInitialMessage"), isUser: false },
  ]);

  // If user hasn't started chatting, keep the initial bot line in sync with language switching.
  React.useEffect(() => {
    setChatMessages((prev) => {
      if (prev.length !== 1) return prev;
      if (prev[0]?.isUser) return prev;
      return [{ text: t("compliance.chatbotInitialMessage"), isUser: false }];
    });
  }, [lang, t]);

  const handleStartAnalysis = async () => {
    if (!analysisFile?.file_id) {
      message.error(t("compliance.noFileSelectedForAnalysis"));
      return;
    }

    setLoading(true);
    try {
      message.loading(
        t("compliance.loadingReportFor", {
          name: analysisFile?.name || "",
        }),
        0
      );

      const reportData = await apiService.getReportByFileId(
        analysisFile.file_id,
        analysisFile.analysis_scope_key
      );
      setMarkdownContent(reportData.content);
      setReportFile(reportData.report_file);

      message.destroy();
      message.success(t("compliance.reportLoaded"));
      onAnalysisComplete?.(reportData);
    } catch (error) {
      message.destroy();
      message.error(
        t("compliance.failedToLoadReport", {
          error: error instanceof Error ? error.message : String(error),
        })
      );
      console.error("Report loading error:", error);
    } finally {
      setLoading(false);
    }
  };

  const handleChatSend = async (content: string) => {
    if (!analysisFile?.file_id) {
      message.error(t("compliance.noFileSelectedForAnalysis"));
      setChatMessages((prev) => [...prev, { text: t("compliance.chatbotNoFile"), isUser: false }]);
      return;
    }

    // Add user + thinking placeholder
    setChatMessages((prev) => [
      ...prev,
      { text: content, isUser: true },
      { text: t("chat.thinking"), isUser: false },
    ]);

    try {
      const response = await apiService.sendMessageForFile(analysisFile.file_id, {
        message: content,
        include_context: true,
        session_id: `report:${analysisFile.file_id}`,
      });

      setChatMessages((prev) => {
        const next = prev.slice(0, -1); // remove thinking
        return [...next, { text: response.response, isUser: false }];
      });
    } catch (error) {
      console.error("Compliance chat error:", error);
      message.error(t("chat.failedToSend", { error: String(error) }));
      setChatMessages((prev) => {
        const next = prev.slice(0, -1);
        return [...next, { text: t("chat.genericError"), isUser: false }];
      });
    }
  };

  const handleChatClear = () => {
    setChatMessages([{ text: t("compliance.chatbotInitialMessage"), isUser: false }]);
  };

  if (!markdownContent && !loading) {
    return (
      <Card>
        <div style={{ textAlign: "center", padding: "40px" }}>
          <FileTextOutlined style={{ fontSize: "48px", color: "#1890ff", marginBottom: "16px" }} />
          <h3>{t("compliance.title")}</h3>
          <p style={{ color: "#666", marginBottom: "24px" }}>{t("compliance.subtitle")}</p>
          <Button type="primary" size="large" onClick={handleStartAnalysis}>
            {t("compliance.loadLatestReport")}
          </Button>
        </div>
      </Card>
    );
  }

  if (loading) {
    return (
      <Card>
        <div style={{ textAlign: "center", padding: "40px" }}>
          <Spin size="large" />
          <h3 style={{ marginTop: "16px" }}>{t("compliance.loadingTitle")}</h3>
          <p style={{ color: "#666" }}>{t("compliance.loadingDesc")}</p>
        </div>
      </Card>
    );
  }

  return (
    <div style={{ padding: "16px" }}>
      <Alert
        title={t("compliance.loadedAlertTitle")}
        description={
          reportFile
            ? t("compliance.displayingReport", { reportFile })
            : t("compliance.latestReport")
        }
        type="success"
        showIcon
        style={{ marginBottom: "16px" }}
      />

      <Card
        title={t("compliance.title")}
        extra={
          <Button type="primary" onClick={handleStartAnalysis} loading={loading}>
            {t("compliance.refreshReport")}
          </Button>
        }
        style={{ marginBottom: "16px" }}
      >
        <div
          style={{
            maxHeight: "700px",
            overflowY: "auto",
            backgroundColor: "#ffffff",
            padding: "24px",
            borderRadius: "8px",
            border: "1px solid #f0f0f0",
            boxShadow: "0 2px 8px rgba(0,0,0,0.06)",
          }}
        >
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {markdownContent || ""}
          </ReactMarkdown>
        </div>
      </Card>

      <Card
        title={<span style={{ fontWeight: 600, color: "#262626" }}>{t("compliance.chatbotTitle")}</span>}
        style={{
          borderRadius: "12px",
          border: "1px solid #e6e6e6",
          boxShadow: "0 2px 8px rgba(0, 0, 0, 0.04)",
        }}
      >
        <div style={{ height: 520 }}>
          <ChatInterface
            messages={chatMessages}
            onSendMessage={handleChatSend}
            onClearChat={handleChatClear}
            onReferenceClick={() => {}}
          />
        </div>
      </Card>
    </div>
  );
};

export default ComplianceAnalysis;
