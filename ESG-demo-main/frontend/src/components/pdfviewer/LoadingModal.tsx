import React from "react";
import { Modal, Progress } from "antd";

interface LoadingModalProps {
  isOpen: boolean;
  progress: number;
  onClose: () => void;
}

const LoadingModal: React.FC<LoadingModalProps> = ({
  isOpen,
  progress,
  onClose,
}) => {
  return (
    <Modal title="Loading Chat" open={isOpen} onCancel={onClose} footer={null}>
      <div className="p-4">
        <Progress percent={progress} status="active" />
        <p className="text-center mt-3 text-gray-600">
          {progress < 100 ? "Loading chat..." : "Chat ready!"}
        </p>
      </div>
    </Modal>
  );
};

export default LoadingModal;
