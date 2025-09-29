import React from "react";
import { Layout, Upload } from "antd";
import { InboxOutlined } from "@ant-design/icons";
import type { UploadFile } from "antd/es/upload/interface";

const { Content } = Layout;
const { Dragger } = Upload;

interface UploadAreaProps {
  onBeforeUpload: (file: UploadFile) => boolean;
}

const UploadArea: React.FC<UploadAreaProps> = ({ onBeforeUpload }) => {
  const props = {
    name: "file",
    multiple: true,
    beforeUpload: onBeforeUpload,
    showUploadList: false,
  };

  return (
    <Layout
      style={{
        margin: 12,
        padding: "0 12px 24px",
        background: "#fff",
        borderRadius: 10,
      }}>
      <Content
        style={{
          padding: 24,
          margin: 0,
          minHeight: 280,
          background: "#fff",
          borderRadius: 8,
        }}>
        <Dragger {...props}>
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">
            Click or drag files to this area to upload
          </p>
          <p className="ant-upload-hint">Support PDF,DOCX files</p>
        </Dragger>
      </Content>
    </Layout>
  );
};

export default UploadArea;
