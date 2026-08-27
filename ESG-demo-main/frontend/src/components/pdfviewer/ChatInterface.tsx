import React, { useState } from "react";
import { Input, Button, Popconfirm, Tooltip } from "antd";
import { CloseOutlined, DeleteOutlined, LoadingOutlined } from "@ant-design/icons";
import { useT } from "@/i18n/useT";

interface Message {
  text: string;
  isUser: boolean;
}

interface ChatInterfaceProps {
  messages: Message[];
  onSendMessage: (message: string) => void;
  onClearChat?: () => void;
  onReferenceClick: (page: number) => void;
  onClose?: () => void;
}

const ChatInterface: React.FC<ChatInterfaceProps> = ({
  messages,
  onSendMessage,
  onClearChat,
  onClose,
}) => {
  const { t } = useT();
  const [inputMessage, setInputMessage] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  const handleSendMessage = async () => {
    if (inputMessage.trim()) {
      setIsLoading(true);
      try {
        onSendMessage(inputMessage);
        setInputMessage("");
      } catch (error) {
        console.error("Failed to send message:", error);
      } finally {
        setIsLoading(false);
      }
    }
  };

  // Function to convert markdown-style bold to HTML
  const formatMessage = (text: string) => {
    return text.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
  };

  return (
    <div className="p-3 h-full flex flex-col min-h-0">
      <div className="flex justify-between items-center mb-3">
        <h3 className="text-lg font-semibold text-gray-800">{t("chat.title")}</h3>
        <div className="flex items-center gap-1">
          <Tooltip title={t("chat.clearTitle")}>
            <Popconfirm
              title={t("chat.clearTitle")}
              description={t("chat.clearDesc")}
              onConfirm={onClearChat}
              okText={t("common.yes")}
              cancelText={t("common.no")}
            >
              <Button
                type="text"
                icon={<DeleteOutlined />}
                className="text-gray-500 hover:text-red-500"
                aria-label={t("chat.clearTitle")}
              />
            </Popconfirm>
          </Tooltip>
          {onClose && (
            <Tooltip title={t("common.close")}>
              <Button
                type="text"
                icon={<CloseOutlined />}
                className="text-gray-500 hover:text-gray-900"
                aria-label={t("common.close")}
                onClick={onClose}
              />
            </Tooltip>
          )}
        </div>
      </div>

      <div
        className="mb-3 min-h-0 flex-1 overflow-y-auto overscroll-y-auto rounded-lg border bg-white p-3"
        data-testid="compliance-message-scroll"
        style={{
          overscrollBehaviorY: "auto",
          WebkitOverflowScrolling: "touch",
        }}
      >
        {messages.length === 0 ? (
          <p className="text-gray-500 text-center">{t("chat.empty")}</p>
        ) : (
          <>
            {messages.map((msg, index) => (
              <div
                key={index}
                className={`mb-2 p-2 rounded-lg w-fit break-words whitespace-pre-wrap ${
                  msg.isUser ? "bg-blue-100 ml-auto" : "bg-gray-100"
                } max-w-[75%]`}
                dangerouslySetInnerHTML={{ __html: formatMessage(msg.text) }}
              />
            ))}
            {isLoading && (
              <div className="flex items-center gap-2 bg-gray-100 p-2 rounded-lg w-fit">
                <LoadingOutlined className="animate-spin" />
                <span>{t("chat.thinking")}</span>
              </div>
            )}
          </>
        )}
      </div>

      <div className="flex gap-2">
        <Input.TextArea
          value={inputMessage}
          onChange={(e) => setInputMessage(e.target.value)}
          placeholder={t("chat.placeholder")}
          autoSize={{ minRows: 1, maxRows: 4 }}
          className="flex-1 overscroll-y-auto"
          style={{
            overscrollBehaviorY: "auto",
            WebkitOverflowScrolling: "touch",
          }}
          onPressEnter={(e) => {
            if (!e.shiftKey) {
              e.preventDefault();
              handleSendMessage();
            }
          }}
        />
        <Button type="primary" onClick={handleSendMessage}>
          {t("chat.send")}
        </Button>
      </div>
    </div>
  );
};

export default ChatInterface;
