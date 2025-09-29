import React, { useState, useEffect } from "react";
import { Modal, Badge, Descriptions, Button, Space, Divider } from "antd";
import { 
  CheckCircleOutlined, 
  CloseCircleOutlined, 
  SyncOutlined,
  ApiOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  BarChartOutlined
} from "@ant-design/icons";
import { apiService } from "@/lib/api";
import type { SystemStatus } from "@/lib/api";

interface SystemStatusMonitorProps {
  open: boolean;
  onClose: () => void;
}

const SystemStatusMonitor: React.FC<SystemStatusMonitorProps> = ({ open, onClose }) => {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);

  const fetchStatus = async () => {
    try {
      console.log('Fetching system status...');
      console.log('API Base URL:', process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000');
      setLoading(true);
      
      const response = await apiService.getSystemStatus();
      console.log('System status response:', response);
      setStatus(response);
      setLastUpdate(new Date());
    } catch (error: any) {
      console.error('Failed to fetch system status:', error);
      console.error('Error message:', error?.message);
      console.error('Error details:', error);
      
      // Show a user-friendly error message
      setStatus({
        status: 'error',
        components: {
          report_loaded: false,
          metrics_loaded: false,
          assessment_available: false,
          llm_configured: false
        }
      } as SystemStatus);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) {
      fetchStatus();
      // 每30秒自动刷新一次
      const interval = setInterval(fetchStatus, 30000);
      return () => clearInterval(interval);
    }
  }, [open]);

  const getStatusBadge = (isActive: boolean) => {
    return isActive ? (
      <Badge status="success" text="Active" />
    ) : (
      <Badge status="default" text="Inactive" />
    );
  };

  const getStatusIcon = (isActive: boolean) => {
    return isActive ? (
      <CheckCircleOutlined style={{ color: '#52c41a' }} />
    ) : (
      <CloseCircleOutlined style={{ color: '#d9d9d9' }} />
    );
  };

  return (
    <Modal
      title={
        <Space>
          <ApiOutlined />
          Backend System Status
          {lastUpdate && (
            <span style={{ fontSize: '12px', color: '#999', marginLeft: 16 }}>
              Last Update: {lastUpdate.toLocaleTimeString()}
            </span>
          )}
        </Space>
      }
      open={open}
      onCancel={onClose}
      width={800}
      footer={[
        <Button 
          key="refresh"
          icon={<SyncOutlined spin={loading} />} 
          onClick={fetchStatus}
          loading={loading}
        >
          Refresh
        </Button>,
        <Button key="close" onClick={onClose}>
          Close
        </Button>
      ]}
    >
      {status ? (
        <>
          {/* 系统总体状态 */}
          <Descriptions size="small" column={1} bordered>
            <Descriptions.Item label="System Status">
              <Space>
                {getStatusIcon(status.status === "operational")}
                <Badge 
                  status={status.status === "operational" ? "success" : "error"} 
                  text={status.status.toUpperCase()} 
                />
              </Space>
            </Descriptions.Item>
          </Descriptions>

          <Divider />

          {/* 组件状态 */}
          <div style={{ marginBottom: 16 }}>
            <h4 style={{ marginBottom: 12 }}>
              <DatabaseOutlined /> Components Status
            </h4>
            <Descriptions size="small" column={2} bordered>
              <Descriptions.Item label="Report Loaded">
                <Space>
                  {getStatusIcon(status.components.report_loaded)}
                  {getStatusBadge(status.components.report_loaded)}
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="Metrics Loaded">
                <Space>
                  {getStatusIcon(status.components.metrics_loaded)}
                  {getStatusBadge(status.components.metrics_loaded)}
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="Assessment Available">
                <Space>
                  {getStatusIcon(status.components.assessment_available)}
                  {getStatusBadge(status.components.assessment_available)}
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="LLM Configured">
                <Space>
                  {getStatusIcon(status.components.llm_configured)}
                  {getStatusBadge(status.components.llm_configured)}
                </Space>
              </Descriptions.Item>
            </Descriptions>
          </div>

          {/* 报告信息 */}
          {status.report_info && (
            <div style={{ marginBottom: 16 }}>
              <h4 style={{ marginBottom: 12 }}>
                <FileTextOutlined /> Report Information
              </h4>
              <Descriptions size="small" column={1} bordered>
                <Descriptions.Item label="Document ID">
                  <code>{status.report_info.document_id}</code>
                </Descriptions.Item>
                <Descriptions.Item label="Segments Count">
                  <Badge count={status.report_info.segments_count} showZero color="blue" />
                </Descriptions.Item>
              </Descriptions>
            </div>
          )}

          {/* 指标信息 */}
          {status.metrics_info && (
            <div>
              <h4 style={{ marginBottom: 12 }}>
                <BarChartOutlined /> Metrics Information
              </h4>
              <Descriptions size="small" column={1} bordered>
                <Descriptions.Item label="Collection ID">
                  <code>{status.metrics_info.collection_id}</code>
                </Descriptions.Item>
                <Descriptions.Item label="Metrics Count">
                  <Badge count={status.metrics_info.metrics_count} showZero color="green" />
                </Descriptions.Item>
              </Descriptions>
            </div>
          )}
        </>
      ) : (
        <div style={{ textAlign: 'center', padding: '20px' }}>
          <SyncOutlined spin style={{ fontSize: '24px', marginBottom: '8px' }} />
          <p>Loading system status...</p>
        </div>
      )}
    </Modal>
  );
};

export default SystemStatusMonitor;