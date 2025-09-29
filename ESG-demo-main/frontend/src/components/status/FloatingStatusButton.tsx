import React, { useState } from "react";
import { FloatButton } from "antd";
import { MonitorOutlined } from "@ant-design/icons";
import SystemStatusMonitor from "./SystemStatus";

const FloatingStatusButton: React.FC = () => {
  const [isModalOpen, setIsModalOpen] = useState(false);

  const showModal = () => {
    setIsModalOpen(true);
  };

  const hideModal = () => {
    setIsModalOpen(false);
  };

  return (
    <>
      <FloatButton
        icon={<MonitorOutlined />}
        tooltip="Backend System Status"
        onClick={showModal}
        style={{
          right: 24,
          bottom: 80,
        }}
        type="primary"
      />
      <SystemStatusMonitor open={isModalOpen} onClose={hideModal} />
    </>
  );
};

export default FloatingStatusButton;