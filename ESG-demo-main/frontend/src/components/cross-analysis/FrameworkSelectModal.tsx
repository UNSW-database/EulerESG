"use client";

import React, { useEffect, useMemo, useState } from "react";
import { Form, Modal, Select } from "antd";
import { useT } from "@/i18n/useT";

import { industries, SASB_OTHER_INDUSTRY_KEY } from "@/data/industries";
import { CDP_TOPIC_OPTIONS } from "@/data/cdpTopics";
import { ACTIVE_FRAMEWORK_OPTIONS, isActiveFramework } from "@/data/frameworkOptions";

export type FrameworkSelectionValues = {
  framework: string;
  industry?: string;
  semiIndustry?: string;
};

type Props = {
  open: boolean;
  initialValues?: Partial<FrameworkSelectionValues>;
  onCancel: () => void;
  onConfirm: (values: FrameworkSelectionValues) => void;
  title?: string;
};

function safeTrim(v: any): string {
  if (v === null || v === undefined) return "";
  return String(v).trim();
}

export default function FrameworkSelectModal({
  open,
  initialValues,
  onCancel,
  onConfirm,
  title = undefined,
}: Props) {
  const { t } = useT();
  const [form] = Form.useForm<FrameworkSelectionValues>();
  const [selectedIndustry, setSelectedIndustry] = useState<string>(safeTrim(initialValues?.industry));

  // Keep form values in sync with initial values when the modal opens.
  useEffect(() => {
    if (!open) return;
    const candidateFramework = safeTrim(initialValues?.framework);
    const initFramework = isActiveFramework(candidateFramework) ? candidateFramework.toUpperCase() : "";
    const initIndustry = safeTrim(initialValues?.industry);
    const initSemi = safeTrim(initialValues?.semiIndustry);
    setSelectedIndustry(initIndustry);
    form.setFieldsValue({
      framework: initFramework || undefined,
      industry: initIndustry || undefined,
      semiIndustry: initSemi || undefined,
    } as any);
  }, [open, initialValues?.framework, initialValues?.industry, initialValues?.semiIndustry, form]);

  const framework = Form.useWatch("framework", form);
  const isSASBSelected = framework === "SASB";
  const isCDPSelected = framework === "CDP";
  const isTopicOnlyFramework = isCDPSelected;

  const industryOptions = useMemo(() => Object.keys(industries || {}), []);

  const semiIndustryOptions = useMemo(() => {
    const key = safeTrim(selectedIndustry);
    if (!key) return [];
    const list = (industries as any)[key];
    return Array.isArray(list) ? list : [];
  }, [selectedIndustry]);

  const handleOk = async () => {
    const values = await form.validateFields();
    if (!isActiveFramework(values.framework)) {
      form.setFields([
        { name: "framework", errors: [t("upload.pleaseSelectFramework")] },
      ]);
      return;
    }

    const out: FrameworkSelectionValues = {
      framework: safeTrim(values.framework).toUpperCase(),
    };
    if (out.framework === "SASB") {
      out.industry = safeTrim(values.industry) || undefined;
      out.semiIndustry = safeTrim(values.semiIndustry) || undefined;
    } else if (out.framework === "CDP") {
      out.industry = "CDP";
      out.semiIndustry = safeTrim(values.semiIndustry) || undefined;
    }
    onConfirm(out);
  };

  return (
    <Modal
      open={open}
      title={title ?? t("cross.selectFramework")}
      okText={t("cross.confirm")}
      cancelText={t("common.cancel")}
      onCancel={onCancel}
      onOk={handleOk}
      destroyOnHidden
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          framework: "",
          industry: "",
          semiIndustry: "",
        }}
      >
        <Form.Item
          name="framework"
          label={t("upload.framework")}
          rules={[{ required: true, message: t("upload.pleaseSelectFramework") }]}
        >
          <Select
            placeholder={t("upload.selectFramework")}
            options={ACTIVE_FRAMEWORK_OPTIONS}
            onChange={(value) => {
              if (value === "SASB") {
                form.setFieldsValue({ semiIndustry: undefined } as any);
                return;
              }
              setSelectedIndustry("");
              form.setFieldsValue({ industry: undefined, semiIndustry: undefined } as any);
            }}
          />
        </Form.Item>

        <Form.Item
          name="industry"
          label={t("upload.industry")}
          rules={[{ required: isSASBSelected, message: t("upload.pleaseSelectIndustry") }]}
          hidden={isTopicOnlyFramework}
        >
          <Select
            placeholder={t("upload.selectIndustry")}
            disabled={!isSASBSelected}
            options={industryOptions.map((industry) => ({
              value: industry,
              label:
                industry === SASB_OTHER_INDUSTRY_KEY
                  ? t("upload.sasbIndustryOther")
                  : industry,
            }))}
            onChange={(value) => {
              const v = safeTrim(value);
              setSelectedIndustry(v);
              form.setFieldsValue({ semiIndustry: undefined } as any);
            }}
          />
        </Form.Item>

        <Form.Item
          name="semiIndustry"
          label={isCDPSelected ? t("upload.cdpTopic") : t("upload.subIndustry")}
          rules={[
            {
              required: isSASBSelected || isTopicOnlyFramework,
              message: isCDPSelected
                ? t("upload.pleaseSelectCdpTopic")
                : t("upload.pleaseSelectSubIndustry"),
            },
          ]}
        >
          <Select
            placeholder={isCDPSelected ? t("upload.selectCdpTopic") : t("upload.selectSubIndustry")}
            disabled={isTopicOnlyFramework ? false : !isSASBSelected || !selectedIndustry}
            allowClear={isTopicOnlyFramework}
            options={
              isCDPSelected
                ? CDP_TOPIC_OPTIONS.map((option) => ({
                    value: option.slug,
                    label: option.label,
                  }))
                : semiIndustryOptions.map((semiIndustry) => ({
                    value: semiIndustry,
                    label: semiIndustry,
                  }))
            }
          />
        </Form.Item>
      </Form>
    </Modal>
  );
}
