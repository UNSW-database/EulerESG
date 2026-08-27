import React, { useEffect, useMemo, useState } from "react";
import { Card, Form, Input, InputNumber, Select, Space } from "antd";
import type { UploadFile } from "antd/es/upload/interface";
import type { FormInstance } from "antd/es/form";
import { industries, SASB_OTHER_INDUSTRY_KEY } from "@/data/industries";
import { CDP_TOPIC_OPTIONS } from "@/data/cdpTopics";
import { isActiveFramework, UPLOAD_FRAMEWORK_OPTIONS } from "@/data/frameworkOptions";
import { useT } from "@/i18n/useT";
import { apiService } from "@/lib/api";
import type { CompanySummary } from "@/lib/api";

export type FileInfoFormValues = {
  category: string;
  description: string;
  tags: string[];
  industry: string;
  /** SASB sub-industries or CDP topic slugs (multi-select = one PDF, multiple compliance JSONs). */
  semiIndustry: string | string[];
  framework: string;
  griSector?: string;
  /** GRI topic slugs (multi-select supported). */
  griTopics?: string[];
  companyId: string;
  companyName?: string;
  reportYears?: Array<number | undefined>;
};

interface FileInfoFormProps {
  form: FormInstance<FileInfoFormValues>;
  selectedUploadFiles: UploadFile[];
  selectedIndustry: string;
  onIndustryChange: (value: string) => void;
  uploadMode: "single" | "multi";
}

interface GriOption {
  slug: string;
  label: string;
}

function detectReportYear(filename: string): number | undefined {
  const matches = Array.from(String(filename || "").matchAll(/(?<!\d)((?:19|20)\d{2})(?!\d)/g));
  const value = matches.at(-1)?.[1];
  return value ? Number(value) : undefined;
}

const FileInfoForm: React.FC<FileInfoFormProps> = ({
  form,
  selectedUploadFiles,
  selectedIndustry,
  onIndustryChange,
  uploadMode,
}) => {
  const { t } = useT();
  const framework = Form.useWatch("framework", form);
  const griSector = Form.useWatch("griSector", form);
  const selectedCompanyId = Form.useWatch("companyId", form);
  const scopeLocked = uploadMode === "multi" && Boolean(selectedCompanyId && selectedCompanyId !== "__new__");
  const isSASBSelected = framework === "SASB";
  const isGRISelected = framework === "GRI";
  const isCDPSelected = framework === "CDP";

  const [griOptions, setGriOptions] = useState<{
    sectors: GriOption[];
    topicsBySector: Record<string, GriOption[]>;
  }>({ sectors: [], topicsBySector: {} });
  const [companies, setCompanies] = useState<CompanySummary[]>([]);

  useEffect(() => {
    if (uploadMode !== "multi") {
      setCompanies([]);
      return;
    }
    apiService
      .getCompanies()
      .then((response) =>
        setCompanies(
          (response.companies || []).filter((company) =>
            isActiveFramework(company.scope_config?.framework)
          )
        )
      )
      .catch(() => setCompanies([]));
  }, [selectedUploadFiles, uploadMode]);

  useEffect(() => {
    if (uploadMode !== "multi") return;
    form.setFieldValue(
      "reportYears",
      selectedUploadFiles.map((file) => detectReportYear(file.name))
    );
  }, [form, selectedUploadFiles, uploadMode]);

  useEffect(() => {
    if (!isGRISelected) return;
    apiService
      .getGriOptions()
      .then(setGriOptions)
      .catch(() => setGriOptions({ sectors: [], topicsBySector: {} }));
  }, [isGRISelected]);

  const griTopicOptions = useMemo(() => {
    if (!griSector || !griOptions.topicsBySector[griSector]) return [];
    return griOptions.topicsBySector[griSector];
  }, [griSector, griOptions.topicsBySector]);

  const totalSizeKB = useMemo(
    () =>
      selectedUploadFiles.reduce((sum, file) => sum + (typeof file.size === "number" ? file.size : 0), 0) /
      1024,
    [selectedUploadFiles]
  );

  const fileTypes = useMemo(() => {
    const types = Array.from(
      new Set(
        selectedUploadFiles
          .map((file) => file.name?.split(".").pop()?.toUpperCase() || file.type || "")
          .filter(Boolean)
      )
    );
    return types.length ? types.join(", ") : t("upload.unknown");
  }, [selectedUploadFiles, t]);

  const handleFrameworkChange = (value: string) => {
    if (value === "SASB") {
      form.setFieldsValue({
        griSector: undefined,
        griTopics: undefined,
        industry: form.getFieldValue("industry"),
        semiIndustry: undefined,
      });
    } else if (value === "GRI") {
      form.setFieldsValue({
        industry: undefined,
        semiIndustry: undefined,
        griSector: form.getFieldValue("griSector"),
        griTopics: form.getFieldValue("griTopics"),
      });
      onIndustryChange("");
    } else if (value === "CDP") {
      form.setFieldsValue({
        industry: undefined,
        griSector: undefined,
        griTopics: undefined,
        semiIndustry: undefined,
      });
      onIndustryChange("");
    } else {
      form.setFieldsValue({
        industry: undefined,
        semiIndustry: undefined,
        griSector: undefined,
        griTopics: undefined,
      });
      onIndustryChange("");
    }
  };

  const handleCompanyChange = (companyId: string) => {
    if (companyId === "__new__") {
      form.setFieldsValue({ companyId, companyName: undefined });
      return;
    }
    const company = companies.find((item) => item.company_id === companyId);
    if (!company) return;
    const scope = company.scope_config || {};
    const scopeSlugs = Array.isArray(scope.scope_slugs) ? scope.scope_slugs : [];
    const frameworkValue = String(scope.framework || "");
    onIndustryChange(String(scope.industry || ""));
    form.setFieldsValue({
      companyId,
      companyName: company.company_name,
      framework: frameworkValue,
      industry: String(scope.industry || ""),
      semiIndustry: frameworkValue === "GRI" ? [] : scopeSlugs,
      griSector: scope.gri_sector || undefined,
      griTopics: frameworkValue === "GRI" ? scopeSlugs : [],
    });
  };

  return (
    <Form<FileInfoFormValues>
      form={form}
      layout="vertical"
      initialValues={{
        category: "document",
        tags: [],
        industry: "",
        semiIndustry: [],
        framework: "",
        griSector: undefined,
        griTopics: [],
        companyId: "__new__",
        reportYears: selectedUploadFiles.map(() => undefined),
      }}
    >
      <Form.Item label={t("upload.fileInformation")}>
        <Space orientation="vertical" style={{ width: "100%" }}>
          <p>
            {t("upload.selectedCount")}: {selectedUploadFiles.length}
          </p>
          <p>
            {t("upload.size")}: {totalSizeKB.toFixed(2)} KB
          </p>
          <p>
            {t("upload.type")}: {fileTypes}
          </p>
          <div className="max-h-28 overflow-y-auto overscroll-y-auto rounded-md border border-gray-200 px-3 py-2 bg-gray-50">
            {selectedUploadFiles.map((file) => (
              <p key={file.uid} className="mb-1 last:mb-0 break-all text-sm text-gray-700">
                {file.name}
              </p>
            ))}
          </div>
        </Space>
      </Form.Item>

      {uploadMode === "multi" && (
        <>
          <Form.Item
            name="companyId"
            label={t("upload.company")}
            rules={[{ required: true, message: t("upload.selectCompany") }]}
          >
            <Select
              onChange={handleCompanyChange}
              options={[
                { label: t("upload.newCompany"), value: "__new__" },
                ...companies.map((company) => ({
                  label: `${company.company_name} (${company.report_count ?? company.report_ids.length}/8)`,
                  value: company.company_id,
                })),
              ]}
            />
          </Form.Item>

          {selectedCompanyId === "__new__" && (
            <Form.Item
              name="companyName"
              label={t("upload.companyName")}
              rules={[{ required: true, whitespace: true, message: t("upload.enterCompanyName") }]}
            >
              <Input maxLength={160} placeholder={t("upload.companyNamePlaceholder")} />
            </Form.Item>
          )}

          <Form.Item label={t("upload.reportYears") }>
            <Space orientation="vertical" style={{ width: "100%" }}>
              {selectedUploadFiles.map((file, index) => (
                <div key={file.uid} className="grid grid-cols-[minmax(0,1fr)_120px] items-center gap-3">
                  <span className="truncate text-sm text-gray-700" title={file.name}>{file.name}</span>
                  <Form.Item name={["reportYears", index]} noStyle>
                    <InputNumber min={1900} max={2100} placeholder={t("upload.yearAuto")} style={{ width: "100%" }} />
                  </Form.Item>
                </div>
              ))}
            </Space>
          </Form.Item>

          <div className="mb-4 text-xs text-gray-500">{t("upload.multiReportLimit")}</div>
        </>
      )}

      <Form.Item
        name="framework"
        label={t("upload.framework")}
        rules={[{ required: true, message: t("upload.pleaseSelectFramework") }]}
      >
        <Select
          placeholder={t("upload.selectFramework")}
          options={UPLOAD_FRAMEWORK_OPTIONS}
          onChange={handleFrameworkChange}
          disabled={scopeLocked}
          style={{ width: "100%" }}
        />
      </Form.Item>

      {/* SASB: Industry + Sub-industry */}
      {isSASBSelected && (
        <Card
          size="small"
          style={{
            marginBottom: 16,
            borderColor: "var(--ant-colorPrimaryBorder, #91caff)",
            background: "var(--ant-colorPrimaryBg, #e6f4ff)",
          }}
          styles={{ body: { padding: "12px 16px" } }}
        >
          <Form.Item
            name="industry"
            label={t("upload.industry")}
            rules={[{ required: true, message: t("upload.pleaseSelectIndustry") }]}
          >
            <Select
              placeholder={t("upload.selectIndustry")}
              options={Object.keys(industries).map((industry) => ({
                value: industry,
                label:
                  industry === SASB_OTHER_INDUSTRY_KEY
                    ? t("upload.sasbIndustryOther")
                    : industry,
              }))}
              onChange={(value) => {
                onIndustryChange(value);
                form.setFieldsValue({ semiIndustry: undefined });
              }}
              disabled={scopeLocked}
              style={{ width: "100%" }}
            />
          </Form.Item>
          <Form.Item
            name="semiIndustry"
            label={t("upload.subIndustry")}
            rules={[
              { required: true, message: t("upload.pleaseSelectSubIndustry") },
              {
                validator: async (_, v) => {
                  const arr = Array.isArray(v) ? v : v ? [v] : [];
                  if (arr.length < 1) throw new Error(t("upload.pleaseSelectSubIndustry"));
                },
              },
            ]}
          >
            <Select
              mode="multiple"
              allowClear
              maxTagCount="responsive"
              placeholder={t("upload.selectSubIndustry")}
              disabled={!selectedIndustry || scopeLocked}
              style={{ width: "100%" }}
              options={
                selectedIndustry
                  ? industries[selectedIndustry].map((semiIndustry) => ({
                      value: semiIndustry,
                      label: semiIndustry,
                    }))
                  : []
              }
            />
          </Form.Item>
        </Card>
      )}

      {/* CDP: questionnaire topic only */}
      {isCDPSelected && (
        <Card
          size="small"
          style={{
            marginBottom: 16,
            borderColor: "var(--ant-colorWarningBorder, #ffe58f)",
            background: "var(--ant-colorWarningBg, #fffbe6)",
          }}
          styles={{ body: { padding: "12px 16px" } }}
        >
          <Form.Item
            name="semiIndustry"
            label={t("upload.cdpTopic")}
            rules={[
              { required: true, message: t("upload.pleaseSelectCdpTopic") },
              {
                validator: async (_, v) => {
                  const arr = Array.isArray(v) ? v : v ? [v] : [];
                  if (arr.length < 1) throw new Error(t("upload.pleaseSelectCdpTopic"));
                },
              },
            ]}
          >
            <Select
              mode="multiple"
              allowClear
              maxTagCount="responsive"
              placeholder={t("upload.selectCdpTopic")}
              style={{ width: "100%" }}
              disabled={scopeLocked}
              options={CDP_TOPIC_OPTIONS.map((o) => ({ label: o.label, value: o.slug }))}
            />
          </Form.Item>
        </Card>
      )}

      {/* GRI: Sector + Topic */}
      {isGRISelected && (
        <Card
          size="small"
          style={{
            marginBottom: 16,
            borderColor: "var(--ant-colorSuccessBorder, #b7eb8f)",
            background: "var(--ant-colorSuccessBg, #f6ffed)",
          }}
          styles={{ body: { padding: "12px 16px" } }}
        >
          <Form.Item
            name="griSector"
            label={t("upload.griSector")}
            rules={[{ required: true, message: t("upload.selectGriSector") }]}
          >
            <Select
              placeholder={t("upload.selectGriSector")}
              onChange={() => form.setFieldsValue({ griTopics: [] })}
              disabled={scopeLocked}
              style={{ width: "100%" }}
              options={griOptions.sectors.map((s) => ({ label: s.label, value: s.slug }))}
            />
          </Form.Item>
          <Form.Item
            name="griTopics"
            label={t("upload.griTopics")}
            rules={[
              { required: true, message: t("upload.selectGriTopics") },
              {
                validator: async (_, v) => {
                  const arr = Array.isArray(v) ? v : [];
                  if (arr.length < 1) throw new Error(t("upload.selectGriTopics"));
                },
              },
            ]}
          >
            <Select
              mode="multiple"
              allowClear
              maxTagCount="responsive"
              placeholder={t("upload.selectGriTopics")}
              disabled={!griSector || scopeLocked}
              style={{ width: "100%" }}
              options={griTopicOptions.map((s) => ({ label: s.label, value: s.slug }))}
            />
          </Form.Item>
        </Card>
      )}
    </Form>
  );
};

export default FileInfoForm;
