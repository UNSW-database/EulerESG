import React from "react";
import { Modal } from "antd";
import type { UploadFile } from "antd/es/upload/interface";
import type { FormInstance } from "antd/es/form";
import FileInfoForm from "./FileInfoForm";
import type { FileInfoFormValues } from "./FileInfoForm";
import { useT } from "@/i18n/useT";

interface UploadOptionsModalProps {
  isOpen: boolean;
  selectedUploadFiles: UploadFile[];
  selectedIndustry: string;
  onOk: () => void;
  onCancel: () => void;
  onIndustryChange: (value: string) => void;
  form: FormInstance<FileInfoFormValues>;
  uploadMode: "single" | "multi";
  confirmLoading: boolean;
}

const UploadOptionsModal: React.FC<UploadOptionsModalProps> = ({
  isOpen,
  selectedUploadFiles,
  selectedIndustry,
  onOk,
  onCancel,
  onIndustryChange,
  form,
  uploadMode,
  confirmLoading,
}) => {
  const { t } = useT();
  return (
    <Modal
      title={t("upload.uploadOptionsTitle")}
      open={isOpen}
      onOk={onOk}
      onCancel={onCancel}
      width={600}
      okText={t("common.ok")}
      cancelText={t("common.cancel")}
      confirmLoading={confirmLoading}
      mask={{ closable: !confirmLoading }}
    >
      <FileInfoForm
        form={form}
        selectedUploadFiles={selectedUploadFiles}
        selectedIndustry={selectedIndustry}
        onIndustryChange={onIndustryChange}
        uploadMode={uploadMode}
      />
    </Modal>
  );
};

export default UploadOptionsModal;
