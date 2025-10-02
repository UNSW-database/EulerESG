import React, { useState } from "react";
import { Input, Button, Popconfirm, Tooltip } from "antd";
import { LoadingOutlined, DeleteOutlined } from "@ant-design/icons";

interface Message {
  text: string;
  isUser: boolean;
}

interface ChatInterfaceProps {
  messages: Message[];
  onSendMessage: (message: string) => void;
  onClearChat?: () => void;
  onReferenceClick: (page: number) => void;
}

const ChatInterface: React.FC<ChatInterfaceProps> = ({
  messages,
  onSendMessage,
  onClearChat,
}) => {
  const [inputMessage, setInputMessage] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  const handleSendMessage = async () => {
    if (inputMessage.trim()) {
      setIsLoading(true);
      try {
        await onSendMessage(inputMessage);
        setInputMessage("");
      } catch (error) {
        console.error('Failed to send message:', error);
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
    <div className="p-3">
      <div className="flex justify-between items-center mb-3">
        <h3 className="text-lg font-semibold text-gray-800">Chat</h3>
        <Tooltip title="Clear chat history">
          <Popconfirm
            title="Clear chat history"
            description="Are you sure you want to clear all messages?"
            onConfirm={onClearChat}
            okText="Yes"
            cancelText="No">
            <Button
              type="text"
              icon={<DeleteOutlined />}
              className="text-gray-500 hover:text-red-500"
            />
          </Popconfirm>
        </Tooltip>
      </div>
      <div className="h-[40vh] overflow-y-auto mb-3 border rounded-lg p-3 bg-white">
        {messages.length === 0 ? (
          <p className="text-gray-500 text-center">
            Chat messages will appear here...
          </p>
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
                <span>Thinking...</span>
              </div>
            )}
          </>
        )}
      </div>
      <div className="flex gap-2">
        <Input.TextArea
          value={inputMessage}
          onChange={(e) => setInputMessage(e.target.value)}
          placeholder="Type your message..."
          autoSize={{ minRows: 1, maxRows: 4 }}
          className="flex-1"
          onPressEnter={(e) => {
            if (!e.shiftKey) {
              e.preventDefault();
              handleSendMessage();
            }
          }}
        />
        <Button type="primary" onClick={handleSendMessage}>
          Send
        </Button>
      </div>
    </div>
  );
};

export default ChatInterface;
