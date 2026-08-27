import React from "react";
import { Modal, Progress } from "antd";
import { useT } from "@/i18n/useT";

interface LoadingModalProps {
  isOpen: boolean;
  progress: number;
  onClose: () => void;
}

const LoadingModal: React.FC<LoadingModalProps> = ({ isOpen, progress, onClose }) => {
  const { t } = useT();

  return (
    <Modal title={t("loadingModal.title")} open={isOpen} onCancel={onClose} footer={null}>
      <div className="p-4">
        <Progress percent={progress} status="active" />
        <p className="text-center mt-3 text-gray-600">
          {progress < 100 ? t("loadingModal.loading") : t("loadingModal.ready")}
        </p>
      </div>
    </Modal>
  );
};

export default LoadingModal;
