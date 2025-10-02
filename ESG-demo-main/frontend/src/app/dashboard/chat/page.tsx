// app/dashboard/chat/page.tsx
"use client";
import React, { useState } from "react";
import { Breadcrumb, message } from "antd";
import { useRouter } from "next/navigation";
import ChatView from "@/components/pdfviewer/ChatView";
import { useFileStore } from "@/store/useFileStore";
import { apiService, type ChatResponse } from "@/lib/api";

interface Message {
  text: string;
  isUser: boolean;
}

export default function ChatPage() {
  const router = useRouter();
  const [messages, setMessages] = useState<Message[]>([
    {
      text: "Welcome to EulerESG. I can help to answer any questions you have about ESG topics, disclosures, or metrics. How can I assist you today?",
      isUser: false,
    },
  ]);

  const files = useFileStore((state) => state.files);
  const selectedFileId = useFileStore((state) => state.selectedFileId);

  const currentFile = files.find((file) => file.file_id === selectedFileId) || null;

  const handleBackToList = () => {
    router.push("/dashboard");
  };

  const handleSendMessage = async (userMessage: string) => {
    // 添加用户消息
    setMessages(prev => [...prev, { text: userMessage, isUser: true }]);

    // 添加加载状态
    setMessages(prev => [...prev, { text: "Thinking...", isUser: false }]);

    try {
      // 调用后端API
      const response: ChatResponse = await apiService.sendMessage({
        message: userMessage,
        include_context: true
      });

      // 移除加载消息，添加真实响应
      setMessages(prev => {
        const newMessages = prev.slice(0, -1); // 移除加载消息
        return [...newMessages, { text: response.response, isUser: false }];
      });

      // 如果有相关段落，可以在这里处理
      if (response.relevant_segments && response.relevant_segments.length > 0) {
        console.log('Relevant segments:', response.relevant_segments);
      }

    } catch (error) {
      console.error('Chat error:', error);
      message.error(`Failed to send message: ${error}`);
      
      // 移除加载消息，添加错误消息
      setMessages(prev => {
        const newMessages = prev.slice(0, -1);
        return [...newMessages, { 
          text: "Sorry, I can't answer your question at the moment. Please try again later.", 
          isUser: false 
        }];
      });
    }
  };

  const handleClearChat = () => {
    setMessages([
      {
        text: "Welcome to EulerESG. I can help to answer any questions you have about ESG topics, disclosures, or metrics. How can I assist you today?",
        isUser: false,
      },
    ]);
  };

  return (
    <div className="w-full flex flex-col justify-start items-center mx-auto pt-1 min-h-screen">
      <div className="w-[95%]">
        <Breadcrumb
          style={{ margin: 20 }}
          items={[
            {
              title: (
                <a
                  onClick={handleBackToList}
                  className="text-blue-600 hover:text-blue-800 cursor-pointer">
                  Files
                </a>
              ),
            },
            {
              title: (
                <a
                  onClick={handleBackToList}
                  className="text-blue-600 hover:text-blue-800 cursor-pointer">
                  All Files
                </a>
              ),
            },
            {
              title: currentFile?.name || "Chat",
            },
          ]}
          className="mb-2 !text-lg"
        />

        <ChatView
          activeFile={currentFile}
          messages={messages}
          onSendMessage={handleSendMessage}
          onClearChat={handleClearChat}
        />
      </div>
    </div>
  );
}
