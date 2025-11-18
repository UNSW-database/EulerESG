import React, { useEffect } from "react";
import { Table, Button, Space, Tag, Popconfirm, Badge } from "antd";
import type { ColumnsType } from "antd/es/table";
import { DeleteOutlined, CommentOutlined, FileSearchOutlined, SyncOutlined } from "@ant-design/icons";
import { useFileStore } from "@/store/useFileStore";
import type { File } from "@/store/useFileStore";

interface FileTableProps {
  onChatClick: (file: File) => void;
  onAnalysisClick?: (file: File) => void;
}

const FileTable: React.FC<FileTableProps> = ({ onChatClick, onAnalysisClick }) => {
  const files = useFileStore((state) => state.files);
  const loading = useFileStore((state) => state.loading);
  const lastRefresh = useFileStore((state) => state.lastRefresh);
  const loadFilesFromBackend = useFileStore((state) => state.loadFilesFromBackend);

  useEffect(() => {
    console.log('FileTable loading files from backend...');
    loadFilesFromBackend().then(() => {
      console.log('Files loaded:', useFileStore.getState().files);
    });
  }, []);

  const columns: ColumnsType<File> = [
    { title: "Name", dataIndex: "name", key: "name" },
    { title: "Size", dataIndex: "size", key: "size" },
    { title: "Date Uploaded", dataIndex: "dateUploaded", key: "dateUploaded" },
    { title: "Type", dataIndex: "type", key: "type" },
    {
      title: "No. of Pages",
      dataIndex: "pages",
      key: "pages",
      render: (_, record) => record.pages || "-",
    },
    { title: "Industry", dataIndex: "industry", key: "industry" },
    { title: "Sub Industry", dataIndex: "semiIndustry", key: "semiIndustry" },
    {
      title: "Framework",
      dataIndex: "framework",
      key: "framework",
      render: (framework: string) => (
        <Tag
          color={
            framework === "SASB"
              ? "blue"
              : framework === "GRI"
              ? "green"
              : framework === "TCFD"
              ? "purple"
              : "default"
          }>
          {framework}
        </Tag>
      ),
    },
    {
      title: "Status",
      key: "status",
      render: (_, file) => (
        <Tag color={file.status === "ready" ? "success" : "warning"}>
          {file.status === "ready" ? "Ready" : "Pending"}
        </Tag>
      ),
    },
    {
      title: "Actions",
      key: "actions",
      render: (_, file) => (
        <Space>
          <Button
            type="primary"
            size="small"
            icon={<CommentOutlined />}
            onClick={() => onChatClick(file)}
            disabled={file.status !== "ready"}>
            Chat
          </Button>
          <Button
            type="default"
            size="small"
            icon={<FileSearchOutlined />}
            onClick={() => onAnalysisClick && onAnalysisClick(file)}
            disabled={file.status !== "ready"}>
            Compliance Analysis
          </Button>
          <Popconfirm
            title="Delete the file"
            description="Are you sure you want to delete this file?"
            onConfirm={async () => {
              console.log('Delete button clicked for file:', file.file_id);
              try {
                await useFileStore.getState().deleteFile(file.file_id!);
                console.log('File deleted successfully');
              } catch (error) {
                console.error('Error deleting file:', error);
              }
            }}
            okText="Yes"
            cancelText="No">
            <Button
              type="default"
              danger
              size="small"
              icon={<DeleteOutlined />}>
              Delete
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div className="mt-4 bg-white rounded-lg shadow-sm">
      <div className="p-3 border-b border-gray-200 flex justify-between items-center">
        <h3 className="text-lg font-semibold text-gray-700">Your Files</h3>
        <div className="flex items-center space-x-2">
          {loading && (
            <Badge
              status="processing"
              text="Loading..."
            />
          )}
          <Button
            size="small"
            icon={<SyncOutlined spin={loading} />}
            onClick={() => loadFilesFromBackend()}
            disabled={loading}
          >
            Refresh
          </Button>
          {lastRefresh > 0 && (
            <span className="text-xs text-gray-500">
              Last updated: {new Date(lastRefresh).toLocaleTimeString()}
            </span>
          )}
        </div>
      </div>
      <div className="overflow-x-auto">
        <Table
          columns={columns}
          dataSource={files}
          pagination={false}
          className="w-full"
          rowKey="key"
          loading={loading}
        />
      </div>
    </div>
  );
};

export default FileTable;
