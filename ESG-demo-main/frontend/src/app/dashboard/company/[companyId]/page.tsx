"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { App as AntdApp, Button, Select, Space, Spin, Tag, Typography } from "antd";
import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import { useParams, useRouter } from "next/navigation";
import { apiService } from "@/lib/api";
import { warmAppRoute } from "@/lib/routeWarmup";
import { useT } from "@/i18n/useT";

const { Title, Text } = Typography;

const AssessmentTable = dynamic(() => import("antd/es/table"), {
  ssr: false,
  loading: () => (
    <div
      aria-busy="true"
      aria-label="Loading assessment table"
      className="h-96 animate-pulse bg-slate-50"
    />
  ),
});

function statusColor(status: string) {
  if (status === "fully_disclosed") return "green";
  if (status === "partially_disclosed") return "gold";
  if (status === "analysis_failed") return "red";
  return "default";
}

export default function CompanyAssessmentPage() {
  const params = useParams<{ companyId: string }>();
  const router = useRouter();
  const { lang } = useT();
  const { message } = AntdApp.useApp();
  const zh = lang === "zh";
  const companyId = decodeURIComponent(String(params?.companyId || ""));
  const [company, setCompany] = useState<any>(null);
  const [assessment, setAssessment] = useState<any>(null);
  const [scope, setScope] = useState<string | undefined>();
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (selectedScope?: string) => {
    if (!companyId) return;
    setLoading(true);
    try {
      const [companyResponse, assessmentResponse] = await Promise.all([
        apiService.getCompany(companyId),
        apiService.getCompanyAssessment(companyId, selectedScope),
      ]);
      setCompany(companyResponse.company);
      setAssessment(assessmentResponse);
      const resolvedScope = selectedScope || assessmentResponse?.assessment?.scope_key;
      if (resolvedScope) setScope(resolvedScope);
    } catch (error: any) {
      void message.error(error?.message || (zh ? "加载公司综合结果失败" : "Failed to load company results"));
    } finally {
      setLoading(false);
    }
  }, [companyId, message, zh]);

  useEffect(() => {
    void load();
  }, [load]);

  const rows = useMemo(() => {
    const values = assessment?.assessment?.metric_analyses;
    return Array.isArray(values) ? values : [];
  }, [assessment]);

  const scopeOptions = useMemo(
    () => (company?.assessment_outputs || []).map((item: any) => ({
      label: item.scope_key,
      value: item.scope_key,
    })),
    [company]
  );

  const columns = [
    {
      title: zh ? "指标" : "Metric",
      key: "metric",
      width: 340,
      render: (_: any, row: any) => row.Metric || row.metric_name || "-",
    },
    {
      title: "Code",
      key: "code",
      width: 150,
      render: (_: any, row: any) => row.Code || row.metric_code || "-",
    },
    {
      title: zh ? "状态" : "Status",
      key: "status",
      width: 150,
      render: (_: any, row: any) => {
        const status = String(row.disclosure_status || row["Disclosure Status"] || "not_disclosed");
        return <Tag color={statusColor(status)}>{status.replaceAll("_", " ")}</Tag>;
      },
    },
    {
      title: zh ? "数值" : "Value",
      key: "value",
      width: 140,
      render: (_: any, row: any) => row.value ?? row.Value ?? "n/a",
    },
    {
      title: zh ? "年份" : "Year",
      key: "year",
      width: 100,
      render: (_: any, row: any) => row.selected_year ?? row["Selected Year"] ?? "-",
    },
    {
      title: zh ? "证据来源" : "Evidence sources",
      key: "sources",
      width: 300,
      render: (_: any, row: any) => {
        const sources = Array.isArray(row.evidence_sources) ? row.evidence_sources : [];
        if (!sources.length) return "-";
        return sources
          .map((source: any) => {
            const name = source.source_report_name || source.source_report_id || (zh ? "报告" : "Report");
            const page = source.data_page ? `${zh ? "第" : "p."}${source.data_page}${zh ? "页" : ""}` : "";
            return `${name}${page ? ` (${page})` : ""}`;
          })
          .join("; ");
      },
    },
  ];

  return (
    <main className="min-h-screen bg-gray-50 px-4 py-5 md:px-8">
      <div className="mx-auto max-w-[1600px]">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <Space>
            <Button
              icon={<ArrowLeftOutlined />}
              onPointerDown={() => warmAppRoute(router, "/dashboard")}
              onFocus={() => warmAppRoute(router, "/dashboard")}
              onMouseEnter={() => warmAppRoute(router, "/dashboard")}
              onClick={() => router.push("/dashboard")}
            />
            <div>
              <Title level={3} style={{ margin: 0 }}>
                {company?.company_name || (zh ? "公司综合结果" : "Company assessment")}
              </Title>
              <Text type="secondary">
                {zh ? "综合报告" : "Reports"}: {company?.report_ids?.length || 0}
                {assessment?.analysis_version ? ` · v${assessment.analysis_version}` : ""}
              </Text>
              {assessment?.stale && <Tag color="warning" className="ml-2">Stale</Tag>}
            </div>
          </Space>
          <Space>
            {scopeOptions.length > 1 && (
              <Select
                value={scope}
                options={scopeOptions}
                style={{ minWidth: 220 }}
                onChange={(value) => {
                  setScope(value);
                  void load(value);
                }}
              />
            )}
            <Button icon={<ReloadOutlined />} onClick={() => void load(scope)}>
              {zh ? "刷新" : "Refresh"}
            </Button>
          </Space>
        </div>

        <div className="border border-gray-200 bg-white">
          {loading ? (
            <div className="flex min-h-72 items-center justify-center"><Spin /></div>
          ) : (
            <AssessmentTable
              rowKey={(row: any, index) => row.metric_id || `${row.Code || row.metric_code}-${index}`}
              columns={columns}
              dataSource={rows}
              size="small"
              scroll={{ x: 1180 }}
              pagination={{ defaultPageSize: 25, showSizeChanger: true }}
            />
          )}
        </div>
      </div>
    </main>
  );
}
