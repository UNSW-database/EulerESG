import React from "react";
import { Modal } from "antd";
import type { UploadFile } from "antd/es/upload/interface";
import type { FormInstance } from "antd/es/form";
import FileInfoForm from "./FileInfoForm";

interface UploadOptionsModalProps {
  isOpen: boolean;
  selectedUploadFile: UploadFile | null;
  selectedIndustry: string;
  onOk: () => void;
  onCancel: () => void;
  onIndustryChange: (value: string) => void;
  form: FormInstance<{
    category: string;
    description: string;
    tags: string[];
    industry: string;
    semiIndustry: string;
    framework: string;
  }>;
}

const UploadOptionsModal: React.FC<UploadOptionsModalProps> = ({
  isOpen,
  selectedUploadFile,
  selectedIndustry,
  onOk,
  onCancel,
  onIndustryChange,
  form,
}) => {
  return (
    <Modal
      title="Upload Options"
      open={isOpen}
      onOk={onOk}
      onCancel={onCancel}
      width={600}>
      <FileInfoForm
        form={form}
        selectedUploadFile={selectedUploadFile}
        selectedIndustry={selectedIndustry}
        onIndustryChange={onIndustryChange}
      />
    </Modal>
  );
};

export default UploadOptionsModal;
