"use client";

import { useMemo } from "react";
import { Modal, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";

import { useT } from "@/i18n/useT";
import type { File } from "@/store/useFileStore";

export type DashboardReportSelectorMode =
  | "compliance"
  | "cross"
  | "disclosure";

type DashboardReportSelectorProps = {
  mode: DashboardReportSelectorMode;
  readyReports: File[];
  selectedReportKeys: string[];
  onSelectionChange: (keys: string[]) => void;
  onReportClick: (file: File) => void;
  onConfirm: () => void;
  onCancel: () => void;
};

const reportKey = (file: File) =>
  `${file.file_id}::${file.analysis_scope_key || ""}`;

export default function DashboardReportSelector({
  mode,
  readyReports,
  selectedReportKeys,
  onSelectionChange,
  onReportClick,
  onConfirm,
  onCancel,
}: DashboardReportSelectorProps) {
  const { lang, t } = useT();
  const isMultiReportSelector = mode === "cross" || mode === "disclosure";

  const columns = useMemo<ColumnsType<File>>(
    () => [
      { title: t("files.columns.name"), dataIndex: "name", key: "name", ellipsis: true },
      { title: t("files.columns.size"), dataIndex: "size", key: "size", width: 100 },
      { title: t("files.columns.dateUploaded"), dataIndex: "dateUploaded", key: "dateUploaded", width: 120 },
      { title: t("files.columns.type"), dataIndex: "type", key: "type", width: 85 },
      {
        title: t("files.columns.framework"),
        dataIndex: "framework",
        key: "framework",
        width: 110,
        render: (value?: string) => <Tag>{value || t("common.unknown")}</Tag>,
      },
      {
        title: t("files.columns.industry"),
        key: "option",
        width: 150,
        ellipsis: true,
        render: (_value, file) => {
          const framework = (file.framework || "").trim();
          return (framework === "CDP" || framework === "TCFD"
            ? file.semiIndustry
            : file.industry) || t("common.unknown");
        },
      },
      {
        title: t("files.columns.subOption"),
        dataIndex: "semiIndustry",
        key: "subOption",
        width: 150,
        ellipsis: true,
        render: (value, file) => {
          const framework = (file.framework || "").trim();
          return framework === "CDP" || framework === "TCFD"
            ? t("common.na")
            : value || t("common.unknown");
        },
      },
      {
        title: t("files.columns.status"),
        key: "status",
        width: 100,
        render: () => <Tag color="success">{t("files.status.ready")}</Tag>,
      },
    ],
    [t],
  );

  const title = mode === "disclosure"
    ? (lang === "zh" ? "选择披露完整度报告" : "Select reports for disclosure completeness")
    : mode === "cross"
      ? (lang === "zh" ? "选择综合分析报告" : "Select reports for cross analysis")
      : (lang === "zh" ? "选择合规分析报告" : "Select a report for compliance analysis");

  return (
    <Modal
      open
      title={title}
      okText={lang === "zh" ? "开始分析" : "Start analysis"}
      cancelText={t("common.cancel")}
      onOk={onConfirm}
      onCancel={onCancel}
      okButtonProps={{
        disabled: isMultiReportSelector
          ? selectedReportKeys.length < 2
          : selectedReportKeys.length !== 1,
      }}
      width={1100}
      destroyOnHidden
    >
      <Table<File>
        className="dashboard-file-table"
        columns={columns}
        dataSource={readyReports}
        rowKey={reportKey}
        size="small"
        scroll={{ x: 1000 }}
        locale={{
          emptyText: lang === "zh"
            ? "暂无已处理完成的报告"
            : "No processed reports available",
        }}
        pagination={{ pageSize: 8, showSizeChanger: false, hideOnSinglePage: true }}
        rowSelection={{
          type: isMultiReportSelector ? "checkbox" : "radio",
          selectedRowKeys: selectedReportKeys,
          onChange: (keys) => onSelectionChange(keys.map(String)),
        }}
        onRow={(file) => ({
          onClick: () => onReportClick(file),
          className: "cursor-pointer",
        })}
      />
    </Modal>
  );
}
