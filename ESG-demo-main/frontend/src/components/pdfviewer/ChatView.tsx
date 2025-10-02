import React, { useState } from "react";
import dynamic from "next/dynamic";
import AnalysisResults from "./AnalysisResults";
import ChatInterface from "./ChatInterface";
import { PanelLeft } from "lucide-react";

const PDFChatViewer = dynamic(() => import("./PDFChatViewer"), { ssr: false });

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000';

interface FileData {
  key: string;
  name: string;
  size: string;
  dateUploaded: string;
  type: string;
  tableStatus: string;
  imageStatus: string;
  url?: string;
  status: string;
  industry?: string;
  semiIndustry?: string;
}

interface Message {
  text: string;
  isUser: boolean;
}

interface ChatViewProps {
  activeFile: FileData | null;
  messages: Message[];
  onSendMessage: (message: string) => void;
  onClearChat: () => void;
}

const ChatView: React.FC<ChatViewProps> = ({
  activeFile,
  messages,
  onSendMessage,
  onClearChat,
}) => {
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [targetPage, setTargetPage] = useState<number | undefined>(undefined);
  console.log("targetPage", targetPage);

  return (
    <div className="flex flex-col gap-6">
      <AnalysisResults
        fileId={activeFile?.file_id}
        onPageNavigate={(page) => {
          setTargetPage(page);
          if (isCollapsed) {
            setIsCollapsed(false);
          }
        }}
      />
      <div className="flex flex-col md:flex-row gap-6">
        <div
          className={`${
            isCollapsed ? "w-full md:w-1/4" : "w-full md:w-1/2"
          } bg-white rounded-lg shadow-sm p-4 hover:scale-[1.02] hover:shadow-lg transition-transform duration-300`}>
          <div className="flex justify-between items-center mb-4">
            <h3 className="text-lg font-semibold text-gray-800 truncate">
              {activeFile?.name || "Document"}
            </h3>
            <button
              onClick={() => setIsCollapsed(!isCollapsed)}
              className="p-1 hover:bg-gray-100 rounded-md transition-transform duration-300"
              title={isCollapsed ? "Expand PDF view" : "Collapse PDF view"}>
              <PanelLeft className="w-5 h-5 text-gray-600" />
            </button>
          </div>
          {activeFile?.type?.toUpperCase() === "PDF" && activeFile?.name && activeFile?.file_id ? (
            <div className="overflow-hidden rounded-lg h-[calc(100%-3rem)]">
              <PDFChatViewer
                fileUrl={`${API_BASE_URL}/api/files/${activeFile.file_id}/pdf`}
                targetPage={targetPage}
              />
            </div>
          ) : (
            <p className="text-gray-500">
              {!activeFile?.file_id ? "File not available" : "Unsupported file type"}
            </p>
          )}
        </div>
        <div
          className={`${
            isCollapsed ? "w-full md:w-3/4" : "w-full md:w-1/2"
          } bg-white rounded-lg shadow-sm p-4 hover:scale-[1.02] hover:shadow-lg transition-transform duration-300`}>
          <ChatInterface
            messages={messages}
            onSendMessage={onSendMessage}
            onClearChat={onClearChat}
            onReferenceClick={(page) => setTargetPage(page)}
          />
        </div>
      </div>
    </div>
  );
};

export default ChatView;
