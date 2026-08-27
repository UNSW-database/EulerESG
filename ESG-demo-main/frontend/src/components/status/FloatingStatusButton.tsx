import React, { useState } from "react";
import { FloatButton } from "antd";
import { MonitorOutlined } from "@ant-design/icons";
import SystemStatusMonitor from "./SystemStatus";
import { useT } from "@/i18n/useT";

const FloatingStatusButton: React.FC = () => {
  const { t } = useT();
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
        tooltip={t("statusPanel.backendSystemStatus")}
        onClick={showModal}
        style={{
          right: 24,
          bottom: 72,
        }}
        type="primary"
      />
      <SystemStatusMonitor open={isModalOpen} onClose={hideModal} />
    </>
  );
};

export default FloatingStatusButton;
