import React, { useRef } from "react";
import { Layout, Upload } from "antd";
import { InboxOutlined } from "@ant-design/icons";
import type { UploadFile } from "antd/es/upload/interface";
import { useT } from "@/i18n/useT";

const { Content } = Layout;
const { Dragger } = Upload;

interface UploadAreaProps {
  onBeforeUpload: (files: UploadFile[]) => void;
  uploadMode: "single" | "multi";
}

const UploadArea: React.FC<UploadAreaProps> = ({
  onBeforeUpload,
  uploadMode,
}) => {
  const { t } = useT();
  const batchKeyRef = useRef<string>("");

  const props = {
    name: "file",
    multiple: uploadMode === "multi",
    accept: ".pdf,application/pdf",
    showUploadList: false,
    beforeUpload: (file: any, fileList: any[]) => {
      const list = (fileList || []) as UploadFile[];
      const batchKey = list.map((item) => `${item.uid}:${item.name}`).join("|");
      const firstUid = list[0]?.uid;
      if (file?.uid === firstUid && batchKey && batchKeyRef.current !== batchKey) {
        batchKeyRef.current = batchKey;
        onBeforeUpload(list);
        window.setTimeout(() => {
          if (batchKeyRef.current === batchKey) batchKeyRef.current = "";
        }, 0);
      }
      return Upload.LIST_IGNORE;
    },
  };

  return (
    <Layout
      className="h-full w-full"
      data-testid="upload-area-shell"
      style={{
        margin: 0,
        padding: "0 4px 12px",
        background: "#fff",
        borderRadius: 10,
      }}
    >
      <Content
        style={{
          padding: "12px 4px",
          margin: 0,
          minHeight: 180,
          background: "#fff",
          borderRadius: 8,
        }}
      >
        <Dragger
          {...props}
          aria-label={t("upload.draggerText")}
          style={{
            padding: "20px 0",
          }}
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">{t("upload.draggerText")}</p>
        </Dragger>
      </Content>
    </Layout>
  );
};

export default UploadArea;
