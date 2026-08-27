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
import { useT } from "@/i18n/useT";
import type { SystemStatus } from "@/lib/api";
import { errorSummary } from "@/lib/logger";

interface SystemStatusMonitorProps {
  open: boolean;
  onClose: () => void;
}

const SystemStatusMonitor: React.FC<SystemStatusMonitorProps> = ({ open, onClose }) => {
  const { t, lang } = useT();
  const locale = lang === "zh" ? "zh-CN" : "en-US";
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);

  const fetchStatus = async () => {
    try {
      setLoading(true);
      
      const response = await apiService.getSystemStatus();
      setStatus(response);
      setLastUpdate(new Date());
    } catch (error: any) {
      console.error(`Failed to fetch system status: ${errorSummary(error)}`);
      
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
      <Badge status="success" text={t("statusPanel.active")} />
    ) : (
      <Badge status="default" text={t("statusPanel.inactive")} />
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
          {t("statusPanel.backendSystemStatus")}
          {lastUpdate && (
            <span style={{ fontSize: '12px', color: '#999', marginLeft: 16 }}>
              {t("files.lastUpdated", { time: lastUpdate.toLocaleTimeString(locale) })}
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
          {t("common.refresh")}
        </Button>,
        <Button key="close" onClick={onClose}>
          {t("statusPanel.close")}
        </Button>
      ]}
    >
      {status ? (
        <>
          {/* 系统总体状态 */}
          <Descriptions
            size="small"
            column={1}
            bordered
            items={[
              {
                key: "system-status",
                label: t("statusPanel.systemStatus"),
                children: (
                  <Space>
                    {getStatusIcon(status.status === "operational")}
                    <Badge
                      status={status.status === "operational" ? "success" : "error"}
                      text={status.status.toUpperCase()}
                    />
                  </Space>
                ),
              },
            ]}
          />

          <Divider />

          {/* 组件状态 */}
          <div style={{ marginBottom: 16 }}>
            <h4 style={{ marginBottom: 12 }}>
              <DatabaseOutlined /> {t("statusPanel.componentsStatus")}
            </h4>
            <Descriptions
              size="small"
              column={2}
              bordered
              items={[
                {
                  key: "report-loaded",
                  label: t("statusPanel.reportLoaded"),
                  children: (
                    <Space>
                      {getStatusIcon(status.components.report_loaded)}
                      {getStatusBadge(status.components.report_loaded)}
                    </Space>
                  ),
                },
                {
                  key: "metrics-loaded",
                  label: t("statusPanel.metricsLoaded"),
                  children: (
                    <Space>
                      {getStatusIcon(status.components.metrics_loaded)}
                      {getStatusBadge(status.components.metrics_loaded)}
                    </Space>
                  ),
                },
                {
                  key: "assessment-available",
                  label: t("statusPanel.assessmentAvailable"),
                  children: (
                    <Space>
                      {getStatusIcon(status.components.assessment_available)}
                      {getStatusBadge(status.components.assessment_available)}
                    </Space>
                  ),
                },
                {
                  key: "llm-configured",
                  label: t("statusPanel.llmConfigured"),
                  children: (
                    <Space>
                      {getStatusIcon(status.components.llm_configured)}
                      {getStatusBadge(status.components.llm_configured)}
                    </Space>
                  ),
                },
              ]}
            />
          </div>

          {/* 报告信息 */}
          {status.report_info && (
            <div style={{ marginBottom: 16 }}>
              <h4 style={{ marginBottom: 12 }}>
                <FileTextOutlined /> {t("statusPanel.reportInformation")}
              </h4>
              <Descriptions
                size="small"
                column={1}
                bordered
                items={[
                  {
                    key: "document-id",
                    label: t("statusPanel.documentId"),
                    children: <code>{status.report_info.document_id}</code>,
                  },
                  {
                    key: "segments-count",
                    label: t("statusPanel.segmentsCount"),
                    children: (
                      <Badge count={status.report_info.segments_count} showZero color="blue" />
                    ),
                  },
                ]}
              />
            </div>
          )}

          {/* 指标信息 */}
          {status.metrics_info && (
            <div>
              <h4 style={{ marginBottom: 12 }}>
                <BarChartOutlined /> {t("statusPanel.metricsInformation")}
              </h4>
              <Descriptions
                size="small"
                column={1}
                bordered
                items={[
                  {
                    key: "collection-id",
                    label: t("statusPanel.collectionId"),
                    children: <code>{status.metrics_info.collection_id}</code>,
                  },
                  {
                    key: "metrics-count",
                    label: t("statusPanel.metricsCount"),
                    children: (
                      <Badge count={status.metrics_info.metrics_count} showZero color="green" />
                    ),
                  },
                ]}
              />
            </div>
          )}
        </>
      ) : (
        <div style={{ textAlign: 'center', padding: '20px' }}>
          <SyncOutlined spin style={{ fontSize: '24px', marginBottom: '8px' }} />
          <p>{t("statusPanel.loadingSystemStatus")}</p>
        </div>
      )}
    </Modal>
  );
};

export default SystemStatusMonitor;
